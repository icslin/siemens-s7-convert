import os
import shutil
import copy
import sqlite3
import time as time_lib
from datetime import datetime
from typing import Dict, List, Any, Optional
import public_lib
public_lib.run_path(__file__)
class SqliteAutoDB:
    """
    SQLite自动分月归档缓存入库管理类
    新增功能：自动检测本轮消失的数据库，落地缓存并关闭连接释放资源
    核心功能：
    1. 内存缓存批量写入，减少数据库IO
    2. 每条采集数据独立时间戳，深拷贝防止引用覆盖
    3. 系统跨月自动归档上月数据库，新建当月库
    4. 跨月前置强制落地缓存，避免只缓存不入库问题
    5. 分数据库独立缓存，互不干扰
    6. 采集字典库减少时，自动关闭闲置库连接并清空缓存
    """
    def __init__(self, db_root_path: str = "./sqlite_data", cache_max_num=50, cache_flush_sec=60):
        """
        初始化数据库管理对象
        :param db_root_path: 数据库主存储目录
        :param cache_max_num: 缓存最大条数，达到自动落盘
        :param cache_flush_sec: 缓存超时秒数，超时自动落盘
        """
        # 数据库主目录
        self.db_root_path = db_root_path
        # 归档文件夹完整路径
        self.archive_root = os.path.join(self.db_root_path, "归档")
        # 创建主目录与归档目录，不存在则自动生成
        if not os.path.exists(self.db_root_path):
            os.makedirs(self.db_root_path, exist_ok=True)
        if not os.path.exists(self.archive_root):
            os.makedirs(self.archive_root, exist_ok=True)

        # 数据库连接池：{库名: {"conn":连接对象, "cursor":游标, "path":文件路径}}
        self.db_pool: Dict[str, dict] = {}
        # 缓存阈值配置
        self.cache_max_num = cache_max_num
        self.cache_flush_sec = cache_flush_sec
        # 分库缓存容器：{库名: {"cache":缓存列表, "last_time":上次写入时间戳}}
        # cache列表结构：[(采集日期, 时分秒, 测点深拷贝字典), ...]
        self.db_cache_map: Dict[str, dict] = {}
        # 记录上一轮写入的数据库集合，用于对比消失的库
        self.last_db_names: set = set()

    def _get_current_year_month(self) -> str:
        """获取当前系统年月字符串，格式 202607"""
        return datetime.now().strftime("%Y%m")

    def _get_now_time(self) -> tuple[str, str]:
        """获取当前采集时间，返回日期+时分秒"""
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        return date_str, time_str

    def _get_db_file_path(self, db_name: str) -> str:
        """根据库名拼接完整db文件路径"""
        return os.path.join(self.db_root_path, f"{db_name}.db")

    def _close_single_conn(self, db_name: str):
        """关闭指定数据库连接，释放文件占用（Windows归档移动文件必须）"""
        if db_name in self.db_pool:
            info = self.db_pool[db_name]
            try:
                info["conn"].close()
            except Exception:
                # 关闭失败不抛出异常，容错处理
                pass
            # 连接从池删除
            del self.db_pool[db_name]
            # print(f"【资源释放】已关闭 {db_name} 数据库连接")

    def archive_db_by_month(self, db_name: str):
        """
        按月归档历史数据库
        逻辑：读取库内最早记录年月，和当前年月不一致则归档
        归档规则：归档/年月/库名_年月.db
        """
        current_ym = self._get_current_year_month()
        db_file = self._get_db_file_path(db_name)
        # 文件不存在直接跳过
        if not os.path.exists(db_file):
            return

        # 归档前强制关闭该库连接，释放文件占用
        self._close_single_conn(db_name)

        # 临时打开数据库读取最早记录年月
        temp_conn = sqlite3.connect(db_file)
        temp_cur = temp_conn.cursor()
        record_ym = current_ym  # 默认当前年月
        try:
            # 查询库内第一张业务数据表（排除sqlite系统表）
            temp_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1;")
            table_row = temp_cur.fetchone()
            if table_row:
                table = table_row[0]
                # 查询该表最早一条数据的日期
                temp_cur.execute(f"SELECT record_date FROM {table} ORDER BY id ASC LIMIT 1;")
                date_row = temp_cur.fetchone()
                if date_row and date_row[0]:
                    record_date = date_row[0]
                    record_ym = datetime.strptime(record_date, "%Y-%m-%d").strftime("%Y%m")
        except Exception:
            # 空库/无表/读取失败，直接关闭临时连接退出归档
            temp_conn.close()
            return
        temp_conn.close()

        # 库内数据年月 != 当前年月，执行归档移动
        if record_ym != current_ym:
            archive_dir = os.path.join(self.archive_root, record_ym)
            os.makedirs(archive_dir, exist_ok=True)
            new_file_name = f"{db_name}_{record_ym}.db"
            target_path = os.path.join(archive_dir, new_file_name)
            # 移动并重命名数据库文件
            shutil.move(db_file, target_path)
            # print(f"【归档完成】{db_name}.db 归档至 {target_path}")

    def _check_month_flush_before_write(self, db_name: str, new_data_ym: str):
        """
        写入缓存前置跨月校验（核心修复逻辑）
        作用：缓存数据月份与当前采集月份不一致时，先落地全部缓存、归档旧库，防止只缓存不入库
        :param db_name: 目标库名
        :param new_data_ym: 当前新采集数据的年月
        """
        # 当前库无缓存直接跳过校验
        if db_name not in self.db_cache_map or len(self.db_cache_map[db_name]["cache"]) == 0:
            return

        cache_list = self.db_cache_map[db_name]["cache"]
        # 取缓存第一条记录的日期，解析缓存所属年月
        first_rec_date = cache_list[0][0]
        cache_ym = datetime.strptime(first_rec_date, "%Y-%m-%d").strftime("%Y%m")

        # 缓存月份 和 当前采集月份不一致 → 跨月，强制落地缓存并归档
        if cache_ym != new_data_ym:
            print(f"【跨月检测】缓存数据月份{cache_ym}，当前月份{new_data_ym}，先落地历史缓存")
            # 落地所有缓存数据
            self.flush_single_db_cache(db_name)
            # 关闭旧库连接
            self._close_single_conn(db_name)
            # 归档上月旧数据库文件
            self.archive_db_by_month(db_name)

    def _get_db_conn(self, db_name: str):
        """
        获取数据库连接
        不存在则新建连接，开启WAL模式提升读写性能
        """
        # 连接池中已有连接直接返回复用
        if db_name in self.db_pool:
            return self.db_pool[db_name]

        full_path = self._get_db_file_path(db_name)
        conn = sqlite3.connect(full_path, check_same_thread=False)
        cursor = conn.cursor()
        # SQLite性能优化参数
        conn.execute("PRAGMA journal_mode=WAL;")        # 写前日志，支持并发读写
        conn.execute("PRAGMA synchronous=NORMAL;")      # 同步策略平衡速度与安全
        conn.execute("PRAGMA cache_size=-20000;")       # 内存缓存页大小
        conn.execute("PRAGMA temp_store=MEMORY;")       # 临时表存放内存

        # 存入连接池
        self.db_pool[db_name] = {"conn": conn, "cursor": cursor, "path": full_path}
        # print(f"新建数据库连接：{full_path}")
        return self.db_pool[db_name]

    def _init_db_cache(self, db_name: str):
        """指定库不存在缓存容器时初始化缓存结构"""
        if db_name not in self.db_cache_map:
            self.db_cache_map[db_name] = {
                "cache": [],
                "last_time": time_lib.time()
            }

    def close_all(self):
        """程序退出统一释放资源：落地所有缓存、关闭全部数据库连接"""
        # 遍历所有库，强制落地剩余缓存
        for db_name in list(self.db_cache_map.keys()):
            self.flush_single_db_cache(db_name)
        # 关闭全部连接
        for db_name in list(self.db_pool.keys()):
            self._close_single_conn(db_name)
        self.db_cache_map.clear()
        self.last_db_names.clear()
        # print("全部连接关闭，缓存落地完成")

    def _auto_create_table(self, cursor, table: str, field_data: Dict[str, Any]):
        """
        自动建表、自动新增字段
        :param cursor: 数据库游标
        :param table: 表名
        :param field_data: 测点键值字典，用于自动识别字段
        """
        # 基础固定字段：自增ID、采集日期、采集时分秒
        base_cols = [
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "record_date TEXT",
            "record_time TEXT"
        ]
        # 动态测点字段全部存储为TEXT，兼容数字/字符串
        biz_cols = [f"{k} TEXT" for k in field_data.keys()]
        all_cols = base_cols + biz_cols
        # 不存在则创建表
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(all_cols)})")

        # 查询表现有字段，缺失则新增字段
        cursor.execute(f"PRAGMA table_info({table})")
        exist_fields = [row[1] for row in cursor.fetchall()]
        for field in field_data.keys():
            if field not in exist_fields:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {field} TEXT")
        # 创建日期索引，提升历史查询速度
        cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_dt ON {table}(record_date, record_time);")

    def _insert_single_db(self, db_name: str, table_data: Dict, rec_date: str, rec_time: str, curr_ym: str):
        """
        单库数据写入缓存逻辑
        :param db_name: 库名
        :param table_data: 单库下所有表测点数据
        :param rec_date: 本次采集日期
        :param rec_time: 本次采集时分秒
        :param curr_ym: 当前系统年月，用于跨月校验
        """
        self._init_db_cache(db_name)
        # 写入前执行跨月校验，跨月先落地旧缓存归档
        self._check_month_flush_before_write(db_name, curr_ym)

        cache_info = self.db_cache_map[db_name]
        # 深拷贝嵌套字典，避免外部原始字典修改覆盖缓存历史数据
        data_copy = copy.deepcopy(table_data)
        # 将本次采集时间+拷贝数据存入缓存列表
        cache_info["cache"].append((rec_date, rec_time, data_copy))
        # 检查是否达到缓存阈值，满足自动落盘
        self.check_single_db_flush(db_name)

    def _clean_disabled_db(self, curr_db_set: set):
        """
        对比上一轮库名集合，清理本轮消失的数据库
        1. 落地该库全部缓存
        2. 关闭数据库连接
        3. 删除缓存记录
        :param curr_db_set: 当前输入data_dict的所有库名集合
        """
        # 找出上一轮存在、本轮消失的库名
        lost_db_names = self.last_db_names - curr_db_set
        if not lost_db_names:
            return

        for lost_db in lost_db_names:
            # print(f"【检测到停用库】{lost_db}，执行缓存落地并关闭连接")
            # 落地剩余缓存数据
            self.flush_single_db_cache(lost_db)
            # 关闭数据库连接
            self._close_single_conn(lost_db)
            # 删除缓存记录，释放内存
            if lost_db in self.db_cache_map:
                del self.db_cache_map[lost_db]

    def write_data(self, data_dict: Dict[str, Dict]):
        """
        对外统一写入入口方法
        新增逻辑：自动清理本轮不再使用的数据库连接
        :param data_dict: 多层采集字典 {库名:{表名:{测点:值}}}
        """
        rec_date, rec_time = self._get_now_time()
        curr_ym = self._get_current_year_month()
        # 获取当前批次所有库名集合
        curr_db_set = set(data_dict.keys())

        # 核心新增：清理消失的库，落地缓存+关闭连接
        self._clean_disabled_db(curr_db_set)

        # 遍历每个数据库的数据，分别存入对应分库缓存
        for db_name, table_data in data_dict.items():
            self._insert_single_db(db_name, table_data, rec_date, rec_time, curr_ym)

        # 更新上一轮库名集合，用于下一次对比
        self.last_db_names = curr_db_set

    def check_single_db_flush(self, db_name: str):
        """检测单库缓存是否满足落盘条件：条数超限 / 超时"""
        cache_info = self.db_cache_map[db_name]
        now = time_lib.time()
        time_diff = now - cache_info["last_time"]
        cache_count = len(cache_info["cache"])
        # 达到最大条数 或 超时，执行落地
        if cache_count >= self.cache_max_num or time_diff >= self.cache_flush_sec:
            self.flush_single_db_cache(db_name)

    def flush_single_db_cache(self, db_name: str):
        """将指定库全部缓存批量写入数据库，写入后清空缓存"""
        self._init_db_cache(db_name)
        cache_info = self.db_cache_map[db_name]
        cache_list = cache_info["cache"]
        # 无缓存直接退出
        if len(cache_list) == 0:
            return

        # 获取数据库连接，自动触发归档校验
        conn_info = self._get_db_conn(db_name)
        cur = conn_info["cursor"]
        try:
            # 循环遍历缓存每条记录，使用自身独立采集时间写入
            for rec_date, rec_time, table_data in cache_list:
                for table_name, row_data in table_data.items():
                    # 自动建表/补字段
                    self._auto_create_table(cur, table_name, row_data)
                    fields = list(row_data.keys())
                    cols = ["record_date", "record_time"] + fields
                    placeholder = ", ".join(["?"] * len(cols))
                    sql = f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES ({placeholder})"
                    vals = [rec_date, rec_time] + list(row_data.values())
                    cur.execute(sql, vals)
            # 批量提交事务
            conn_info["conn"].commit()
            # print(f"【{db_name}】缓存落地，共{len(cache_list)}条")
        except Exception as e:
            # 写入异常回滚，防止脏数据
            conn_info["conn"].rollback()
            # print(f"【{db_name}】写入失败回滚：{str(e)}")
        finally:
            # 写入完成清空缓存，更新最后操作时间戳
            cache_info["cache"].clear()
            cache_info["last_time"] = time_lib.time()

    def query(self, db_name: str, table_name: str, condition: str = "", limit: Optional[int] = None) -> List[dict]:
        """
        通用查询接口
        :param db_name: 数据库名称
        :param table_name: 表名
        :param condition: where查询条件，空则查全部
        :param limit: 返回条数限制
        :return: 列表，每条数据为字典
        """
        conn_info = self._get_db_conn(db_name)
        cur = conn_info["cursor"]
        sql = f"SELECT * FROM {table_name}"
        if condition:
            sql += f" WHERE {condition}"
        if limit:
            sql += f" LIMIT {limit}"
        cur.execute(sql)
        rows = cur.fetchall()
        # 获取表字段名，映射为字典返回
        cur.execute(f"PRAGMA table_info({table_name})")
        cols = [c[1] for c in cur.fetchall()]
        return [dict(zip(cols, r)) for r in rows]

# 全局单例实例，项目全局共用
SQL_PATH = './snap7/sqlite/'
db_mgr = SqliteAutoDB(db_root_path=SQL_PATH, cache_max_num=60, cache_flush_sec=60)

def record(fname: str, dict_data: Dict[str, Dict]):
    """
    外层统一入库入口函数
    :param fname: 配置的数据库根目录，用于参数校验
    :param dict_data: 采集多层测点数据
    """
    # 路径一致性校验，防止目录配置错误
    if fname != db_mgr.db_root_path:
        raise ValueError(f"路径不一致：入参{fname}，全局{db_mgr.db_root_path}")
    try:
        # 调用类内部写入方法，自动处理时间、缓存、跨月归档
        db_mgr.write_data(dict_data)
    except Exception as e:
        public_lib.rich_info(f"入库异常：{str(e)}")

if __name__ == "__main__":
    import time
    input_data = {
        "数据库1": {
            "制冷主机": {"温度": 18.6, "流量": 225, "状态": 1},
            "水泵": {"current": 12.5, "频率": 42, "运行时间": 1360}
        },
        "数据库2": {
            "制冷主机": {"温度": 18.6, "流量": 225, "状态": 1},
            "水泵": {"current": 12.5, "频率": 42, "运行时间": 1360}
        }
    }
    try:
        del_num = 0 

        ton = 0
        db_mgr.cache_flush_sec = 10
        while True:
            time.sleep(1)
            ton += 1
            print(f"更新采集数据{ton}")
            record(SQL_PATH, input_data)
            if ton > 10:
                ton = 0
    except KeyboardInterrupt:
        print("程序停止，落地所有缓存")
    finally:
        # 程序退出释放资源
        db_mgr.close_all()