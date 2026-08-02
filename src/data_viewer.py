"""
网页数据分析后端服务 类封装版本
功能：SQLite数据库浏览、时序图表聚合查询、CSV导出
使用标准库wsgiref实现WSGI服务，静默无控制台访问日志
"""
import os
import sys
import sqlite3
import time
import uuid
import csv
from io import StringIO
from flask import Flask, request, jsonify, Response,send_from_directory
from flask_cors import CORS
import urllib.parse
import urllib.request
from datetime import datetime
from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler
from socketserver import ThreadingMixIn
import threading
import psutil
from pathlib import Path

import socket
import public_lib
public_lib.run_path(__file__)


def check_port_listen(port: int) -> list:
    """
    查找本机所有监听该端口的连接
    :param port: 目标端口
    :return: 列表 [(监听地址, pid), ...]，空=端口无人监听
    """
    res = []
    # kind="inet" 包含ipv4 tcp
    for conn in psutil.net_connections(kind="inet"):
        # 判断是否为监听状态
        if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port:
            ip_addr = conn.laddr.ip
            pid = conn.pid
            res.append((ip_addr, pid))
    return res

class StaticResourceManager:
    def __init__(self):
        self.resource_list = [
            {
                "url": "https://cdn.bootcdn.net/ajax/libs/vue/3.3.8/vue.global.prod.min.js",
                "save_path": "./static/vue.global.prod.min.js"
            },
            {
                "url": "https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js",
                "save_path": "./static/echarts.min.js"
            },
            {
                "url": "https://cdn.bootcdn.net/ajax/libs/element-plus/2.8.0/index.full.min.js",
                "save_path": "./static/element-plus/index.full.min.js"
            },
            {
                "url": "https://cdn.bootcdn.net/ajax/libs/element-plus/2.8.0/index.min.css",
                "save_path": "./static/element-plus/index.min.css"
            }
        ]
        self.max_retry = 3
        self.timeout = 30
        self.chunk_size = 8192
        self.test_host = "cdn.bootcdn.net"
        self.test_port = 443
        self.net_check_timeout = 5

        self._lock = threading.Lock()
        self.download_finished: bool | None = None   # None未启动，False进行中，True结束
        self.download_result: bool | None = None

    def scan_all_exist(self) -> bool:
        all_exist = True
        for item in self.resource_list:
            dest = Path(item["save_path"])
            if not dest.exists():
                all_exist = False
        return all_exist

    def check_network(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.net_check_timeout)
            sock.connect((self.test_host, self.test_port))
            sock.close()
            return True
        except Exception:
            return False

    def _get_remote_size(self, url: str) -> int | None:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Python stdlib"
            }
            req = urllib.request.Request(url, headers=headers, method="HEAD")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                length = resp.headers.get("Content-Length")
                return int(length) if length else None
        except Exception:
            return None

    def _download_single(self, url: str, dest_path: str) -> bool:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        retry_count = 0
        while retry_count < self.max_retry:
            retry_count += 1
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Python stdlib"
                }
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp, open(dest, "wb") as f:
                    while chunk := resp.read(self.chunk_size):
                        f.write(chunk)

                remote_size = self._get_remote_size(url)
                if remote_size is not None:
                    local_size = dest.stat().st_size
                    if local_size != remote_size:
                        raise Exception("文件大小校验不一致")
                return True
            except Exception as e:
                pass
        return False

    def _download_task(self):
        with self._lock:
            self.download_finished = False
        final_success = False
        if not self.check_network():
            with self._lock:
                self.download_result = False
                self.download_finished = True
            return
        all_ok = True
        for item in self.resource_list:
            dest = Path(item["save_path"])
            if dest.exists():
                continue
            if not self._download_single(item["url"], item["save_path"]):
                all_ok = False
        final_success = all_ok
        with self._lock:
            self.download_result = final_success
            self.download_finished = True

    def start_async_download(self):
        with self._lock:
            if self.download_finished is not None:
                raise RuntimeError("下载任务已启动或已完成，禁止重复调用")
        thread = threading.Thread(target=self._download_task, daemon=True)
        thread.start()

    def cleanup(self):
        self.resource_list.clear()

    def get_status(self) -> tuple[bool | None, bool | None]:
        """线程安全获取状态 (download_finished, download_result)"""
        with self._lock:
            return self.download_finished, self.download_result

    def __del__(self):
        pass


# ===================== WSGI静默服务基础类 =====================
class ChartSilentHandler(WSGIRequestHandler):
    """屏蔽wsgiref原生所有控制台输出"""
    def log_request(self, code='-', size='-'):
        pass
    def log_error(self, format, *args):
        pass
    def log_message(self, format, *args):
        pass

class ChartWSGIServer(ThreadingMixIn, WSGIServer):
    """多线程WSGI服务器"""
    daemon_threads = True


# ===================== Web数据分析服务主类 =====================
class DataWebServer:
    def __init__(self, ip="0.0.0.0", port=8000):
        # 路径常量定义
        self.host = ip
        self.port = port
        self.LOGGER_FILE = 'log_data_viewer.txt'
        self.TMP_FOLDER = "./tmp_db"
        self.LOCAL_DB_ROOT = "./snap7/sqlite"
        self.EXPIRE_SEC = 3600
        # 文件ID映射 {file_id: db_path}
        self.file_map = {}
        # WSGI服务实例
        self._server = None
        self.server_thread = None

        # 创建目录
        os.makedirs(self.TMP_FOLDER, exist_ok=True)
        os.makedirs(self.LOCAL_DB_ROOT, exist_ok=True)

        # 初始化Flask app
        static_path = self.get_resource_path("static")
        self.app = Flask(__name__, 
                        static_folder=static_path,
                        static_url_path='/static')
        self.app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
        CORS(self.app, resources={r"/api/*": {"origins": "*"}})

        self._register_routes()

    def get_resource_path(self, relative_path):
        """兼容Pyinstaller打包路径 —— 打包后指向exe所在目录"""
        if getattr(sys, 'frozen', False):
            base_path = os.path.dirname(sys.executable)
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_path, relative_path)

    def clean_expire(self):
        """清理过期临时数据库文件"""
        now = time.time()
        del_list = []
        for fid, path in self.file_map.items():
            if self.TMP_FOLDER in os.path.abspath(path):
                if not os.path.exists(path) or now - os.path.getmtime(path) > self.EXPIRE_SEC:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    del_list.append(fid)
        for k in del_list:
            self.file_map.pop(k, None)

    def _register_routes(self):
        """注册所有路由接口"""
        app = self.app

        @app.route("/")
        def index_page():
            html_path = self.get_resource_path("index.html")
            with open(html_path, "r", encoding="utf-8") as f:
                return f.read()

        @app.route("/api/local_dbs", methods=["GET"])
        def scan_local_dbs():
            self.clean_expire()
            db_files = []
            suffix_list = (".db", ".sqlite", ".sqlite3")
            for root, _, files in os.walk(self.LOCAL_DB_ROOT):
                for fname in files:
                    if fname.lower().endswith(suffix_list):
                        full_path = os.path.abspath(os.path.join(root, fname))
                        rel_path = os.path.relpath(full_path, start=".")
                        db_files.append({
                            "rel_path": rel_path,
                            "filename": fname,
                            "full_path": full_path
                        })
            return jsonify({"code": 200, "data": db_files})

        @app.route("/api/load_local_db", methods=["POST"])
        def load_local_db():
            self.clean_expire()
            data = request.get_json()
            file_path = data.get("path")
            if not file_path or not os.path.isfile(file_path):
                return jsonify({"code": 404, "msg": "数据库文件不存在"}), 404
            fid = str(uuid.uuid4())
            self.file_map[fid] = file_path
            return jsonify({
                "code": 200,
                "data": {
                    "file_id": fid,
                    "filename": os.path.basename(file_path)
                }
            })

        @app.route("/api/upload", methods=["POST"])
        def upload():
            self.clean_expire()
            if "file" not in request.files:
                return jsonify({"code": 400, "msg": "未选择文件"}), 400
            f = request.files["file"]
            fid = str(uuid.uuid4())
            save_path = os.path.join(self.TMP_FOLDER, f"{fid}_{f.filename}")
            f.save(save_path)
            self.file_map[fid] = save_path
            return jsonify({"code": 200, "data": {"file_id": fid, "filename": f.filename}})

        @app.route("/api/tables", methods=["GET"])
        def get_tables():
            self.clean_expire()
            fid = request.args.get("file_id")
            if fid not in self.file_map:
                return jsonify({"code": 404, "msg": "文件失效"}), 404
            conn = sqlite3.connect(self.file_map[fid], check_same_thread=False)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
            tables = []
            for (t_name,) in cur.fetchall():
                cur.execute(f"PRAGMA table_info(`{t_name}`);")
                fields = [{"name": r[1], "type": r[2]} for r in cur.fetchall()]
                tables.append({"table_name": t_name, "fields": fields})
            conn.close()
            return jsonify({"code": 200, "data": tables})

        @app.route("/api/table_data", methods=["GET"])
        def table_data():
            self.clean_expire()
            fid = request.args.get("file_id")
            t_name = request.args.get("table")
            limit = int(request.args.get("limit", 100))
            offset = int(request.args.get("offset", 0))
            if fid not in self.file_map:
                return jsonify({"code": 404, "msg": "文件失效"}), 404
            conn = sqlite3.connect(self.file_map[fid], check_same_thread=False)
            cur = conn.cursor()
            cur.execute(f"PRAGMA table_info(`{t_name}`);")
            headers = [r[1] for r in cur.fetchall()]
            cur.execute(f"SELECT * FROM `{t_name}` LIMIT ? OFFSET ?", (limit, offset))
            rows = cur.fetchall()
            cur.execute(f"SELECT COUNT(*) FROM `{t_name}`")
            total = cur.fetchone()[0]
            conn.close()
            return jsonify({"code": 200, "data": {"headers": headers, "rows": rows, "total": total}})

        @app.route("/api/chart_data", methods=["GET"])
        def chart_data():
            self.clean_expire()
            fid = request.args.get("file_id")
            t_name = request.args.get("table")
            y_cols = request.args.getlist("y")
            start_t = request.args.get("start", "").strip()
            end_t = request.args.get("end", "").strip()

            if fid not in self.file_map or len(y_cols) == 0:
                return jsonify({"code": 400, "msg": "请至少选择一个Y轴字段"}), 400

            conn = sqlite3.connect(self.file_map[fid], check_same_thread=False)
            cur = conn.cursor()
            y_sql = ",".join([f"`{y}`" for y in y_cols])

            time_expr = "`record_date` || ' ' || `record_time` AS full_datetime"
            sql = f"SELECT {time_expr}, {y_sql} FROM `{t_name}`"

            where = []
            params = []
            if start_t and end_t:
                where.append("(`record_date` || ' ' || `record_time`) BETWEEN ? AND ?")
                params.append(start_t)
                params.append(end_t)

            if where:
                sql += " WHERE " + " AND ".join(where)

            sql += " ORDER BY full_datetime LIMIT 800"
            cur.execute(sql, params)
            rows = cur.fetchall()

            labels = []
            series = {y: [] for y in y_cols}
            for row in rows:
                labels.append(row[0])
                for i, y_name in enumerate(y_cols):
                    val = row[i + 1]
                    try:
                        series[y_name].append(float(val))
                    except (ValueError, TypeError):
                        series[y_name].append(0)
            conn.close()
            return jsonify({"code": 200, "data": {"labels": labels, "series": series}})

        @app.route("/api/chart_agg", methods=["GET"])
        def chart_agg():
            self.clean_expire()
            fid = request.args.get("file_id")
            t_name = request.args.get("table")
            y_cols = request.args.getlist("y")
            start_t = request.args.get("start", "").strip()
            end_t = request.args.get("end", "").strip()

            if fid not in self.file_map or len(y_cols) == 0 or not start_t or not end_t:
                return jsonify({"code": 400, "msg": "请选择起止时间与至少一个字段"}), 400

            conn = sqlite3.connect(self.file_map[fid], check_same_thread=False)
            cur = conn.cursor()

            start_dt = datetime.strptime(start_t, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(end_t, "%Y-%m-%d %H:%M:%S")
            delta_min = (end_dt - start_dt).total_seconds() / 60

            raw_select = ",".join([f"`{col}`" for col in y_cols])
            try:
                full_sql = ""
                params = [start_t, end_t]
                if delta_min <= 30:
                    full_sql = f"""
                    SELECT `record_date` || ' ' || `record_time` AS full_datetime, {raw_select}
                    FROM `{t_name}`
                    WHERE (`record_date` || ' ' || `record_time`) BETWEEN ? AND ?
                    ORDER BY full_datetime
                    """
                elif delta_min <= 6 * 60:
                    full_sql = f"""
                    SELECT full_datetime, {raw_select} FROM (
                        SELECT
                            `record_date` || ' ' || `record_time` AS full_datetime,
                            {raw_select},
                            ROW_NUMBER() OVER(
                                PARTITION BY strftime('%Y-%m-%d %H:%M', `record_date` || ' ' || `record_time`)
                                ORDER BY `record_date` || ' ' || `record_time`
                            ) AS rn
                        FROM `{t_name}`
                        WHERE (`record_date` || ' ' || `record_time`) BETWEEN ? AND ?
                    ) src
                    WHERE rn = 1
                    ORDER BY full_datetime
                    """
                elif delta_min <= 7 * 24 * 60:
                    full_sql = f"""
                    SELECT full_datetime, {raw_select} FROM (
                        SELECT
                            `record_date` || ' ' || `record_time` AS full_datetime,
                            {raw_select},
                            ROW_NUMBER() OVER(
                                PARTITION BY strftime('%s', `record_date` || ' ' || `record_time`) / (15*60)
                                ORDER BY `record_date` || ' ' || `record_time`
                            ) AS rn
                        FROM `{t_name}`
                        WHERE (`record_date` || ' ' || `record_time`) BETWEEN ? AND ?
                    ) src
                    WHERE rn = 1
                    ORDER BY full_datetime
                    """
                else:
                    full_sql = f"""
                    SELECT full_datetime, {raw_select} FROM (
                        SELECT
                            `record_date` || ' ' || `record_time` AS full_datetime,
                            {raw_select},
                            ROW_NUMBER() OVER(
                                PARTITION BY strftime('%Y-%m-%d %H', `record_date` || ' ' || `record_time`)
                                ORDER BY `record_date` || ' ' || `record_time`
                            ) AS rn
                        FROM `{t_name}`
                        WHERE (`record_date` || ' ' || `record_time`) BETWEEN ? AND ?
                    ) src
                    WHERE rn = 1
                    ORDER BY full_datetime
                    """
                cur.execute(full_sql, params)
                rows = cur.fetchall()
            except Exception:
                fallback_sql = f"""
                SELECT `record_date` || ' ' || `record_time` AS full_datetime, {raw_select}
                FROM `{t_name}`
                WHERE (`record_date` || ' ' || `record_time`) BETWEEN ? AND ?
                ORDER BY full_datetime LIMIT 2000
                """
                cur.execute(fallback_sql, params)
                rows = cur.fetchall()

            labels = []
            series = {y: [] for y in y_cols}
            for row in rows:
                labels.append(row[0])
                for i, name in enumerate(y_cols):
                    val = row[i+1]
                    try:
                        series[name].append(float(val))
                    except (ValueError, TypeError):
                        series[name].append(0)
            conn.close()
            return jsonify({"code":200, "data":{"labels":labels, "series":series}})

        @app.route("/api/export_csv", methods=["GET"])
        def export_csv():
            self.clean_expire()
            fid = request.args.get("file_id")
            t_name = request.args.get("table")
            sel_fields = request.args.getlist("field")
            start_t = request.args.get("start", "").strip()
            end_t = request.args.get("end", "").strip()

            if fid not in self.file_map:
                return jsonify({"code": 404, "msg": "文件失效"}), 404
            if not sel_fields:
                return jsonify({"code": 400, "msg": "请先选择需要导出的字段！"}), 400

            export_cols = ["record_date", "record_time"]
            for f in sel_fields:
                if f not in export_cols:
                    export_cols.append(f)
            col_sql = ",".join([f"`{c}`" for c in export_cols])

            conn = sqlite3.connect(self.file_map[fid], check_same_thread=False)
            cur = conn.cursor()

            sql = f"SELECT {col_sql} FROM `{t_name}`"
            params = []
            if start_t and end_t:
                sql += " WHERE (`record_date` || ' ' || `record_time`) BETWEEN ? AND ?"
                params.append(start_t)
                params.append(end_t)
            sql += " ORDER BY record_date, record_time"

            cur.execute(sql, params)
            all_rows = cur.fetchall()
            conn.close()

            output = StringIO()
            writer = csv.writer(output, quoting=csv.QUOTE_ALL)
            writer.writerow(export_cols)
            writer.writerows(all_rows)
            content = output.getvalue().encode("gbk", errors="replace")

            filename_utf8 = f"{t_name}_数据导出.csv"
            filename_url = urllib.parse.quote(filename_utf8)
            headers = {
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{filename_url}"; filename*=utf-8\'\'{filename_url}'
            }
            return Response(content, headers=headers)

        @app.route("/api/clear_cache", methods=["POST"])
        def clear_cache():
            try:
                for filename in os.listdir(self.TMP_FOLDER):
                    file_path = os.path.join(self.TMP_FOLDER, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                    except Exception:
                        pass
                clear_keys = []
                for fid, path in self.file_map.items():
                    if self.TMP_FOLDER in os.path.abspath(path):
                        clear_keys.append(fid)
                for k in clear_keys:
                    self.file_map.pop(k)
                return jsonify({"code":200, "msg":"临时缓存清除成功（本地数据库源文件保留）"})
            except Exception as e:
                return jsonify({"code":500, "msg":str(e)}),500

    def start_threaded(self):
        try:
            mgr = StaticResourceManager()
            all_exist = mgr.scan_all_exist()
            download_success = True
            if not all_exist:
                public_lib.rich_info(self.LOGGER_FILE,1,'前端静态资源状态','下载中，请稍候...')
                mgr.start_async_download()
                wait_start = time.time()
                max_wait = 300  # 最多等待5分钟
                # 主线阻塞等待下载结束
                while True:
                    finished, res = mgr.get_status()
                    if finished is True:
                        download_success = res
                        break
                    if time.time() - wait_start > max_wait:
                        download_success = False
                        public_lib.rich_info(self.LOGGER_FILE,0,'前端静态资源状态','前端静态资源下载等待超时！')
                        break
                    time.sleep(1)

            # 清理实例
            mgr.cleanup()
            del mgr
            if not download_success:
                public_lib.rich_info(self.LOGGER_FILE,0,'前端静态资源状态','❌下载失败，请检查网络或手动下载缺失文件！')
                return

            public_lib.rich_info(self.LOGGER_FILE, 1, '数据查询服务状态',f'启动；监听地址：{self.host}，端口：{self.port}')
            self._server = make_server(
                host=self.host,
                port=self.port,
                app=self.app,
                server_class=ChartWSGIServer,
                handler_class=ChartSilentHandler
            )
            
            self._server.serve_forever()
        except Exception as e:
            public_lib.rich_info(self.LOGGER_FILE,0,'数据查询服务状态',f"启动失败；错误信息：{str(e)}")
            self._server = None
    def start(self):
        listen_port = check_port_listen(self.port)
        if listen_port:
            public_lib.rich_info(self.LOGGER_FILE,0,'数据查询服务状态',f"启动失败；端口{self.port}已被占用，占用进程：{listen_port}")
            return
        else:
            thd0 = threading.Thread(target=self.start_threaded,daemon=True)
            thd0.start()
    def stop(self):
        """优雅关闭WSGI服务"""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            public_lib.rich_info(self.LOGGER_FILE,1,'数据查询服务状态',f'已停止')


def simulate_run(ip='0.0.0.0',port=5008):
    dataViewer = DataWebServer(ip=ip,port=port)
    dataViewer.start()

    while True:
        try:
        # print(dataViewer)
            time.sleep(3)
        except KeyboardInterrupt:
            dataViewer.stop()
            break
        except Exception as e:
            print(e)
            dataViewer.stop()
            break

    

if __name__ == "__main__":
    simulate_run()