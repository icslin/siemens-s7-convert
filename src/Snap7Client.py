'''
说明：使用snap7读取西门子PLC的DB块数据，提供一个Modbus_Tcp服务端并映射数据，通过MQTT上报
    使用了snap7库，modbus_tk库，paho_mqtt模块
    snap7官网：https://snap7.sourceforge.net/
    Paho-mqtt：https://pypi.org/project/paho-mqtt/#description
作者：工控万金油
首发日期：2024年5月18日
包含文件：Snap7Client.py，iec104_Server.py，Modbus_Server.py，Mqtt_Server.py，web_api.py，public_lib.py，Sqlite_OP.py，data_viewer.py
声明：我的代码使用snap7、paho_mqtt、flask等开源模块，感谢他们！

更新记录：
2026年07月28日 - v3.5.10 新增sqlite数据库查询，导出功能;修复远程重启，一致累加设备实例的bug;优化打印区域
2026年07月30日 - v3.5.11 修复图表小数点显示问题，修复超80字段后字段筛选数据显示异常的bug，优化modbus和iec104写逻辑
2026年08月02日 - v3.5.12 新增自动检测前端静态资源，缺失自动下载
2026年08月12日 - v3.5.13 优化mdobus数据显示逻辑；对入库字段含特殊符号进行处理，保证顺利入库
2026年08月14日 - v3.5.14 Snap7Client.py完善注释，数据查询功能优化前端操作，优化写逻辑，消除写与读的冲突导致出现写失败的情况
'''
import snap7
import time
import json
import struct
import copy
import ctypes
import threading as thd
import logging
import public_lib
import snap7.util as util
import web_api
import iec104_Server
import Modbus_Server
import Mqtt_Server
import Sqlite_OP
import data_viewer

VERSION = '3.5.14'                              # 程序版本号
UPDATE = '2026年08月18日'                       # 最近更新日期
LOGGER_FILE = './snap7/log_snap7.txt'           # 运行日志文件路径
WRITE_LOG_FILE = './snap7/log_snap7_Write.txt'  # 写操作日志文件路径
Modbus_Server.LOGGER_FILE = LOGGER_FILE         # 统一Modbus服务端的日志路径
Sqlite_OP.SQL_PATH = './snap7/sqlite/'          # SQLite数据库文件存放目录
Modbus_Server.LOGGER_FILE = LOGGER_FILE         # Modbus服务运行日志名称
public_lib.max_logs_len = 10                    # 日志最大长度  

# 禁用snap7原生日志
logger_snap7 = logging.getLogger('snap7')
logger_snap7.setLevel(logging.CRITICAL)
# 自动检测运行路径
public_lib.run_path(__file__)

# ======================== 统一常量枚举，替换代码内所有魔数 ========================
class PLCParamIndex:
    """PLC参数列表索引常量类。
    每台PLC设备的参数以列表形式存储，通过此处定义的索引统一访问，
    避免代码中出现魔数，提升可读性与可维护性。
    """
    INSTANCE = 0            # 实例      
    DEVICE_IP = 1           # 设备IP
    MODBUS_ID = 2           # modbus的ID
    PLC_NAME = 3            # PLC名称
    MODBUS_DB_SIZE = 4      # modbus的DB块大小
    MODBUS_POINT = 5        # modbus的点位
    SNAP7_POINT = 6         # snap7的点位
    PLC_STATE = 7           # PLC连接状态
    SNAP7_RW_POINT = 9      # snap7有读写权限的点位
    MB_CMP_POINT = 10       # 读写比较点位
    DB_MAN_MIN = 11         # 数据块起始结束地址
    SNAP7_RD_TMP = 12       # snap7读取DB块临时变量
    FIRST_RUN = 13          # 第一次运行标志
    SNAP7_READ_ERR = 14     # 读取失败标志
    SNAP7_READ_EN = 15      # snap7读取数据标志
    PLC_CONNECT_FAIL = 16   # PLC连接失败标志
    SNAP7_READ_SUCCESS = 17 # snap7读取成功标志
    PLC_RACK = 18           # PLC机架号
    PLC_SLOT = 19           # PLC插槽号
    IEC104_POINT = 20       # IEC104点位
    MODBUS_TMP = 21         # modbus临时变量
    IEC104_TMP = 22         # IEC104临时变量
    RECORD_DATA = 23        # 归档变量
    SNAP7_WRITE_EN = 24     # 写入数据标志
    SNAP7_RERR_CNT = 25     # snap7读取失败计数
    MODBUS_LAST_POINT = 26  # 上一modbus点表
    PLC_READ_ERR = 27       # PLC读取错误标志
    PLC_READ_EN = 28
# ======================== 通用转换工具类 ========================
class DataConvertUtil:
    """通用数据转换工具类。
    提供有符号/无符号整数互转、浮点数与双16位寄存器互转、位操作等基础转换，
    供PLC与Modbus/MQTT/WebAPI/IEC104等各协议服务在读写数据时调用。
    """
    @staticmethod
    def uint_to_int16(data):
        """无符号16位整数 转 有符号16位整数。"""
        return ctypes.c_int16(data).value
    @staticmethod
    def int16_to_uint(data):
        """有符号16位整数 转 无符号16位整数。"""
        return ctypes.c_uint16(data).value
    @staticmethod
    def uint32_to_int32(data):
        """无符号32位整数 转 有符号32位整数。"""
        return ctypes.c_int32(data).value
    @staticmethod
    def int32_to_uint32(data):
        """有符号32位整数 转 无符号32位整数。"""
        return ctypes.c_uint32(data).value
    @staticmethod
    def ubyte_to_byte(data):
        """无符号字节(0~255) 转 有符号字节(-128~127)。"""
        return ctypes.c_byte(data).value
    @staticmethod
    def byte_to_ubyte(data):
        """有符号字节(-128~127) 转 无符号字节(0~255)。"""
        return ctypes.c_ubyte(data).value

    @staticmethod
    def float_to_two_uint16(f):
        """将32位浮点数拆分为两个16位无符号整数（高字在前、低字在后），
        用于写入Modbus的两个连续寄存器。
        """
        b = struct.pack('f', f)
        i1, i2 = struct.unpack('HH', b)
        return [i2 & 0xffff, i1 & 0xffff]

    @staticmethod
    def dword_to_two_uint16(value):
        """将32位双字(DWORD)拆分为两个16位无符号整数（高字在前、低字在后）。"""
        temp_H = (value & 0xffff0000) >> 16
        temp_L = value & 0xffff
        return [temp_H & 0xffff, temp_L & 0xffff]

    @staticmethod
    def two_word_to_float(h, l):
        """将高、低两个16位字拼成32位浮点数（大端序），结果保留5位小数。"""
        z0 = hex(h)[2:].zfill(4)
        z1 = hex(l)[2:].zfill(4)
        z = z0 + z1
        result = struct.unpack('!f', bytes.fromhex(z))[0]
        return round(result, 5)

    @staticmethod
    def set_bit(byte_val, n):
        """将字节值的第n位置1，返回新字节值（用于bool写操作的置位）。"""
        mask = 1 << n
        return byte_val | mask

    @staticmethod
    def reset_bit(byte_val, n):
        """将字节值的第n位清0，返回新字节值（用于bool写操作的复位）。"""
        mask = 0xff ^ (1 << n)
        return byte_val & mask

# ======================== 全局运行上下文，收拢所有分散全局变量，消除全局污染 ========================
class GlobalContext:
    """全局运行上下文。
    将原本分散的模块级全局变量（协议配置、PLC运行数据、服务实例、归档配置等）
    收拢到单一对象中统一管理，避免全局变量污染。
    """
    def __init__(self):
        """初始化全局上下文：设置各配置项默认值与运行数据容器。"""
        # 程序生命周期全局开关
        self.program_exit_en = 0
        self.web_api_exit_en = 0
        self.program_state_exit = 0
        # 协议配置参数
        self.mb_ip = '0.0.0.0'        
        self.mb_port = 502            
        self.mbtcp_en = ''             
        self.param_freq = 1            
        self.Ton_Pub = 10              
        self.code_pub = ''
        self.topic = ''               
        self.mqtt_en = ''
        self.mss_topic = ''
        self.web_api_en = 0
        self.webApi_ip = '0.0.0.0'
        self.webApi_port = 5000
        self.iec104_en = 0
        self.iec104_addr = '0.0.0.0'
        self.iec104_port = 2404
        self.iec104_coa = 1
        self.autorun_en = 0
        self.delay_arun = 15
        self.dir_hide = 0
        self.topic_ft = "file_transfer"
        self.topic_fb = "faceback"
        # PLC运行数据
        self.data_all = []
        self.client_plc = None
        self.params = []
        self.temp_para = []
        self.plc_para = []
        # 协议服务实例
        self.server_web_api = None
        self.iec104_server = None
        self.data_viewer_server = None
        # 数据归档配置
        self.record_en = False
        self.record_cycle = 60
        # 数据查询服务配置
        self.query_data_en = False
        self.query_data_ip = '0.0.0.0'
        self.query_data_port = 6688

# 实例化全局上下文
ctx = GlobalContext()
# ======================== Snap7Client 采集核心类 ========================
class Snap7Client():
    """Snap7采集核心类。
    负责通过S7协议连接西门子PLC、周期性批量读取DB块/M区数据、
    按点位数据类型解码，并对外提供统一的读(get)/写(set)接口。
    """
    def __init__(self, params,  rd_freq=1) -> None:
        """初始化采集客户端。
        :param params: 设备参数列表，每项结构见 PLCParamIndex 定义
        :param rd_freq: 读取周期，单位秒
        """
        self.params = copy.deepcopy(params)     
        self.client = [sgl_param[PLCParamIndex.INSTANCE] for sgl_param in params]
        self.keepalive = 1
        self.db_data = {}
        self.freq = rd_freq
        self.exit = 0

    def convert_bytearray(self, data, type, boolnum):
        """将从PLC读取的原始字节数组按数据类型解码为具体数值。
        :param data: 原始字节数组
        :param type: 数据类型（real/int/dint/word/dword/byte/string/char/bool）
        :param boolnum: bool类型时的位号（0~7）
        """
        if type == 'real':
            temp_data = util.get_real(data, 0)
            temp_data = round(temp_data, 3) # 保留三位小数
        elif type == 'int':
            temp_data = util.get_int(data, 0)
        elif type == 'dint':
            temp_data = util.get_dint(data, 0)
        elif type == 'word':
            temp_data = util.get_word(data, 0)
        elif type == 'dword':
            temp_data = util.get_dword(data, 0)
        elif type == 'byte':
            temp_data = util.get_byte(data, 0)
        elif type == 'string':
            temp_data = util.get_string(data, 0)
        elif type == 'char':
            temp_data = util.get_char(data, 0)
        elif type == 'bool':
            temp_data = util.get_bool(data, 0, boolnum)
            temp_data = 1 if temp_data else 0
        else:
            return data
        return temp_data

    def convert_data(self, wdata, dtype, boolnum):
        """将待写入的数值按数据类型编码为PLC可识别的字节数组（写操作前调用）。
        bool类型按位号写入单字节；byte类型自动限幅到0~255。
        """
        if dtype in ['Real', 'real']:
            bry_data = bytearray(4)
            temp_wdata = util.set_real(bry_data, 0, wdata)
        elif dtype in ['DWord', 'dword', 'DInt', 'dint', 'Dword', 'Dint']:
            bry_data = bytearray(4)
            util.set_dword(bry_data, 0, wdata)
            temp_wdata = bry_data
        elif dtype in ['bool', 'Bool']:
            wdata = 1 if wdata >= 1 else 0
            bry_data = bytearray(1)
            temp_wdata = util.set_bool(bry_data, 0, boolnum, wdata)
            return bry_data
        elif dtype in ['Word', 'word', 'Int', 'int']:
            bry_data = bytearray(2)
            temp_wdata = util.set_word(bry_data, 0, wdata)
        elif dtype in ['Byte', 'byte']:
            wdata = 255 if wdata >= 255 else wdata
            bry_data = bytearray(1)
            temp_wdata = util.set_byte(bry_data, 0, wdata)
        return temp_wdata

    def con_plc(self, ip, rack, slot, comm_err):
        """建立与PLC的S7连接。
        :return: (snap7客户端实例, 连接状态1/0, 错误标志1/0)
        """
        try:
            self.plc = snap7.client.Client()
            self.plc.set_connection_type(3)     # 1=PG,2=OP,3=S7Basic
            self.plc.connect(ip, rack, slot)
            self.com_state = self.plc.get_connected()
            if self.com_state:
                public_lib.rich_info(LOGGER_FILE, 1, '设备连接', f'成功连接PLC {ip}')
            return self.plc, 1 if self.com_state else 0, 0  
        except Exception as err:
            if comm_err == 0:
                public_lib.rich_info(LOGGER_FILE, 0, f'Snap7Client>con_plc[{ip}]->', f'{err}')
            return self.plc, 0, 1

    def db_read(self, plcname, db_num, start, size):
        """从指定PLC的DB块读取缓存(SNAP7_RD_TMP)中截取一段数据，
        避免每次取值都直接访问PLC，提高读取效率。
        """
        for sgl_param in self.params:
            if sgl_param[PLCParamIndex.PLC_NAME] == plcname:
                rd_db_datas = sgl_param[PLCParamIndex.SNAP7_RD_TMP]
                for _db_num in rd_db_datas:
                    if db_num == _db_num:
                        return rd_db_datas[_db_num][start:(start + size)]

    def get(self, plcName, db_num, start_addr, size, type):
        """对外读接口：按PLC名、DB块号、起始地址、大小和数据类型读取点位值。
        bool类型支持 "字节地址.位号" 格式的地址（如 "0.1"）。
        """
        start_b = 0
        if type in ['bool', 'Bool']:
            addr = str(start_addr).split('.')
            start_addr = eval(addr[0])
            start_b = eval(addr[1]) if len(addr) == 2 else 0
        else:
            start_addr = int(start_addr)
        for sgl_param in self.params:
            if sgl_param[PLCParamIndex.PLC_STATE] == 0:
                continue
            if sgl_param[PLCParamIndex.PLC_NAME] == plcName:
                tmp_start = start_addr - int(sgl_param[PLCParamIndex.DB_MAN_MIN][db_num][0])
                data = self.db_read(plcName, db_num, tmp_start, size)
                data = self.convert_bytearray(data, type, start_b)
                return data

    def set(self, plcName, db_num, start_addr, data, dtype, device):
        """对外写接口：向指定PLC的DB块或M区写入数据。
        bool类型先读取原字节再置位/清位，避免影响同一字节中的其他位；
        db_num为0时表示写入M区。
        """
        start_b = 0
        if dtype in ['bool', 'Bool']:
            temp = str(start_addr).split('.')
            start_addr = eval(temp[0])
            start_b = eval(temp[1]) if len(temp) == 2 else 0
            if db_num == 0:
                temp_get = device.mb_read(start_addr, 1)
            else:
                temp_get = device.db_read(db_num, start_addr, 1)
            temp_get = util.get_byte(temp_get, 0)
            temp_get = DataConvertUtil.byte_to_ubyte(temp_get)
            if data >= 1:
                data = DataConvertUtil.set_bit(temp_get, start_b)
            else:
                data = DataConvertUtil.reset_bit(temp_get, start_b)
            wdata = self.convert_data(data, 'byte', start_b)
        else:
            start_addr = int(start_addr)
            wdata = self.convert_data(data, dtype, start_b)

        def type2num(dtype):
            """返回数据类型占用的字节宽度：real/dword/dint为4，word/int为2，byte/bool为1。"""
            if dtype in ['Real', 'real', 'DWord', 'dword', 'DInt', 'dint']:
                return 4
            elif dtype in ['int', 'Int', 'Word', 'word']:
                return 2
            elif dtype in ['Byte', 'byte', 'bool', 'Bool']:
                return 1
        size = type2num(dtype)
        for sgl_param in self.params:
            if sgl_param[PLCParamIndex.PLC_STATE] == 0:
                continue
            if sgl_param[PLCParamIndex.PLC_NAME] == plcName:
                if db_num == 0:
                    sgl_param[PLCParamIndex.INSTANCE].mb_write(start_addr, size, wdata)
                else:
                    sgl_param[PLCParamIndex.INSTANCE].db_write(db_num, start_addr, wdata)
        
    def now_read(self, rd_freq=1):
        """周期读取线程主函数：按rd_freq周期批量读取每台在线PLC配置的DB块/M区，
        并将原始数据更新至缓存SNAP7_RD_TMP；包含读取失败计数、断线标记等容错处理。
        """
        sp_ot_cnt = 3  # 允许的连续读取失败次数阈值（基础值）
        # 根据读取频率动态调整最大连续读取失败次数：周期越慢，容忍次数越多
        if rd_freq < 1:
            sp_ot_cnt = 3 * (1/rd_freq )
        elif rd_freq < 20:
            sp_ot_cnt = 3 * rd_freq 
        else:
            sp_ot_cnt = 2 * rd_freq
        # 采集主循环：周期性批量读取所有在线PLC的数据块
        while True:
            if self.exit == 1:
                break
            for sgl_param in self.params:
                if sgl_param[PLCParamIndex.PLC_STATE] == 1 and not sgl_param[PLCParamIndex.SNAP7_WRITE_EN]:
                    db_data = {}
                    for db_num in sgl_param[PLCParamIndex.DB_MAN_MIN]:
                        try:
                            sgl_param[PLCParamIndex.PLC_READ_EN] = True
                            min_addr = int(sgl_param[PLCParamIndex.DB_MAN_MIN][db_num][0])
                            max_addr = int(sgl_param[PLCParamIndex.DB_MAN_MIN][db_num][1])
                            if db_num == 0:
                                data = sgl_param[PLCParamIndex.INSTANCE].mb_read(min_addr, max_addr - min_addr)
                            else:
                                data = sgl_param[PLCParamIndex.INSTANCE].db_read(db_num, min_addr, max_addr - min_addr)
                            db_data[db_num] = copy.deepcopy(data)
                            sgl_param[PLCParamIndex.FIRST_RUN] = 1
                            sgl_param[PLCParamIndex.SNAP7_RERR_CNT] = 0
                            sgl_param[PLCParamIndex.PLC_READ_ERR] = 0
                            sgl_param[PLCParamIndex.PLC_READ_EN] = False
                        except Exception as err:
                            sgl_param[PLCParamIndex.PLC_READ_EN] = False
                            sgl_param[PLCParamIndex.SNAP7_READ_SUCCESS] = 0
                            sgl_param[PLCParamIndex.PLC_READ_ERR] = 1
                            alm_text = str(err)
                            if 'Item not available' in alm_text and sgl_param[PLCParamIndex.SNAP7_READ_ERR] == 0:
                                public_lib.rich_info(LOGGER_FILE, 0, 
                                                     f'{sgl_param[PLCParamIndex.DEVICE_IP]},{sgl_param[PLCParamIndex.PLC_NAME]}', 
                                                     '有些点位的块地址不存在或偏移量超过数据库地址上限，请检查点位块地址及偏移量是否正确！')
                                sgl_param[PLCParamIndex.SNAP7_READ_ERR] = 1
                                break
                            if sgl_param[PLCParamIndex.SNAP7_RERR_CNT] <= sp_ot_cnt :
                                sgl_param[PLCParamIndex.SNAP7_RERR_CNT] += 1
                            else:
                                sgl_param[PLCParamIndex.SNAP7_RERR_CNT] = 0
                                sgl_param[PLCParamIndex.INSTANCE].disconnect()
                                sgl_param[PLCParamIndex.PLC_STATE] = 0
                                public_lib.rich_info(LOGGER_FILE, 0, f'{sgl_param[PLCParamIndex.DEVICE_IP]}已断开连接', err)
                                break
                    sgl_param[PLCParamIndex.SNAP7_READ_SUCCESS] = 1
                    sgl_param[PLCParamIndex.SNAP7_RD_TMP] = copy.deepcopy(db_data)
                else:
                    sgl_param[PLCParamIndex.PLC_READ_EN] = False
                    sgl_param[PLCParamIndex.SNAP7_READ_SUCCESS] = 0
            time.sleep(rd_freq)

    def main(self):
        """连接维护主循环：对离线PLC自动发起重连；
        检测到点位配置错误（块地址不存在等）时退出循环。
        """
        thd1 = thd.Thread(target=self.now_read, args=(self.freq,),daemon=1).start()
        item_err = 0
        while True:
            if self.exit == 1:
                break
            for sgl_param in self.params:
                if sgl_param[PLCParamIndex.PLC_STATE] == 0:
                    sgl_param[PLCParamIndex.FIRST_RUN] = 0
                    sgl_param[PLCParamIndex.INSTANCE], sgl_param[PLCParamIndex.PLC_STATE], sgl_param[PLCParamIndex.PLC_CONNECT_FAIL] = self.con_plc(
                        sgl_param[PLCParamIndex.DEVICE_IP],
                        sgl_param[PLCParamIndex.PLC_RACK],
                        sgl_param[PLCParamIndex.PLC_SLOT],
                        sgl_param[PLCParamIndex.PLC_CONNECT_FAIL]
                    )
                if sgl_param[PLCParamIndex.SNAP7_READ_ERR] == 1:
                    # 配点错误导致无法正常读取，退出线程
                    item_err = 1
                    break
            time.sleep(1)
            if item_err == 1:
                break

# ======================== s7转modbus点位 ========================
def s7_to_mb(dic):
    """将S7点位表转换为Modbus保持寄存器点表。
    real/dword/dint类型占2个寄存器（拆分为高、低字），
    bool/int/word/byte占1个寄存器，暂不支持的类型填充404。
    """
    def convert_value(data):
        """将单个点位的值转换为Modbus寄存器值列表（16位字）。"""
        temp_data = [0, 0]
        if data[3] in ['Real', 'real']:
            temp = DataConvertUtil.float_to_two_uint16(data[-1])
            temp_data[0] = temp[0] & 0xffff
            temp_data[1] = temp[1] & 0xffff
        elif data[3] in ['DWord', 'dword', 'DInt', 'dint']:
            temp = DataConvertUtil.dword_to_two_uint16(data[-1])
            temp_data[0] = temp[0] & 0xffff
            temp_data[1] = temp[1] & 0xffff
        elif data[3] in ['Bool', 'bool', 'Int', 'int', 'Word', 'word', 'Byte', 'byte']:
            del temp_data[-1]
            temp_data[0] = data[-1] & 0xffff
        else:
            del temp_data[0]
            temp_data[-1] = 404
        return temp_data
    return [value for data in dic.values() for value in convert_value(data)]

# ======================== 读取配置文件 ========================
def RD_file(csv, json_fname, id=0):
    """读取并解析点位表CSV配置文件，同时生成JSON点表和Modbus点位表文件。
    :param csv: 点位表CSV文件路径
    :param json_fname: 输出文件名前缀（一般为PLC名称）
    :param id: Modbus站号
    :return: (完整点表字典, Modbus寄存器总长度, 各DB块起止地址字典, IEC104点位数据, 归档表结构)
    """
    def max_min_size(dic):
        """统计每个DB块被点位覆盖的最小起始地址与最大结束地址，
        轮询时只需批量读取该地址范围，减少通信数据量。
        """
        db_dic = {}
        ADDR = 1
        SIZE = 2
        for tag in dic:
            db_num = dic[tag][0]
            while True:
                if db_num in db_dic:
                    key_addr = dic[tag][ADDR]
                    value_size = dic[tag][SIZE]
                    if key_addr in db_dic[db_num].keys():
                        if db_dic[db_num][key_addr] > value_size:
                            value_size = db_dic[db_num][key_addr]
                    db_dic[db_num][key_addr] = value_size
                    break
                else:
                    db_dic[db_num] = {}     # 给db_num键一个空字典
        for db_num in db_dic:
            max_addr = max(db_dic[db_num])
            minAddr = min(db_dic[db_num])
            max_addr_size = db_dic[db_num][max_addr]
            maxAddr = int(max_addr) + max_addr_size
            cmp = max(db_dic[db_num].values())
            if maxAddr - minAddr == 1 and cmp != 1:
                maxAddr += cmp - 1
            result = [minAddr, maxAddr]
            db_dic[db_num] = result
        return db_dic

    def WD_file(nfile, data):
        """将点表字典以格式化JSON写入文件（UTF-8编码）。"""
        with open(nfile, 'w', encoding='utf-8') as f:
            temp = json.dumps(data, indent=1, ensure_ascii=False)
            f.write(temp)

    def WD_modbus_table(nfile, data, tag, id=0):
        """导出Modbus点位表CSV文件，包含名称、地址、长度、数据类型、
        读写权限、上位机标识、上位机数据类型、IEC104地址等列，供组态软件参考。
        """
        with open(nfile, 'w') as f:
            TYPE = 3
            PERMISSION = 4
            MB_ADDR = 6
            MB_LEN = 7
            IEC104_ADDR = 5
            temp_list = ['名称', '地址', '长度', '数据类型', '权限', '上位机数据标识', '上位机数据类型', 'IEC104地址', '\n']
            f.write(f'Modbus服务端地址：{ctx.mb_ip}\nModbus服务端端口：{ctx.mb_port}\nModbus服务端ID：{id}\n')
            f.write(','.join(['名称', '地址', '长度', '数据类型', '权限', '上位机数据标识', '上位机数据类型', 'IEC104地址', '\n']))
            for i in data:
                temp_list[0] = i
                temp_list[1] = str(data[i][MB_ADDR])
                temp_list[2] = str(data[i][MB_LEN])
                temp_list[3] = str(data[i][TYPE])
                temp_list[4] = data[i][PERMISSION]
                temp_list[5] = f'{tag}@{i}'
                if data[i][3] in ['byte', 'int', 'word', 'Byte', 'Int', 'Word']:
                    temp_list[6] = 'Int'
                elif data[i][3] in ['Real', 'real']:
                    temp_list[6] = 'Float'
                elif data[i][3] in ['Dword', 'dword', 'Dint', 'dint']:
                    temp_list[6] = 'Double'
                elif data[i][3] in ['bool', 'Bool']:
                    temp_list[6] = 'Boolean'
                temp_list[7] = str(data[i][IEC104_ADDR])
                f.write(','.join(temp_list))

    def convert_size(Dtype):
        """返回数据类型占用的字节数：real/dword/dint为4字节，word/int为2字节，byte/bool为1字节。"""
        if Dtype in ['Real', 'real', 'DWord', 'Dword', 'dword', 'Dint', 'DInt', 'dint']:
            return 4
        elif Dtype in ['word', 'Word', 'int', 'Int']:
            return 2
        elif Dtype in ['Byte', 'byte', 'bool', 'Bool']:
            return 1

    def convert_list(nfile, ecd):
        """逐行解析点位表CSV，生成点表字典、IEC104点位列表和归档表结构；
        数据类型非法或格式异常的行将被跳过并记录日志。
        """
        with open(nfile, encoding=ecd) as f:
            data = f.readlines()
        del data[0]
        dic = {}
        iec104_list = {i: [] for i in [0x0d, 0x0b, 0x01]}
        record_list = [] #[表名,{变量：值}]
        for i in data:
            if i == '\n':
                continue
            row = i[:-1]            # 去除换行符
            row = row.split(',')    # 按逗号分割
            try:
                row[1] = row[1].lower() # 数据类型转换为小写
                if row[1] not in ['real', 'dint',  'dword',  'word',  'int',   'byte', 'bool', ]:
                    continue
            except Exception as err:
                continue
            table_name = row[6]
            try:
                DataSize = convert_size(row[1])
                try:
                    ioa = int(row[5])
                except:
                    ioa = 0
                dic[row[0]] = [int(row[3]), eval(row[2]), DataSize, row[1], row[4], ioa]
                if table_name != '':
                    record_dict0 = {} #{变量：值}
                    record_dict0[row[0]] = 0
                    record_list.append([row[6], record_dict0])
            except Exception as err:
                public_lib.rich_info(LOGGER_FILE, 0, 'RD_File->convert_list->', err)
            try:
                addr = int(row[5])
                value = 0
                type_id = 0
                wd_id = 0
                if row[1] in ['real', 'Real']:
                    type_id = 0x0d
                    wd_id = 50
                elif row[1] in ['int', 'Int']:
                    type_id = 0x0b
                    wd_id = 49
                elif row[1] in ['bool', 'Bool']:
                    type_id = 0x01
                    wd_id = 45
                single_list = [addr, value, type_id, wd_id, row[0]]
                iec104_list[type_id].append(single_list)
            except Exception:
                pass
        record_dict_result = {} #{表名：{变量：值}}
        for table_name, field_dict in record_list:
            # 如果表不存在，新建空字典
            if table_name not in record_dict_result:
                record_dict_result[table_name] = {}
            # 合并当前字段到对应表
            record_dict_result[table_name].update(field_dict)
        return dic, iec104_list ,record_dict_result

    def add_mb_db(dic):
        """为每个点位分配Modbus寄存器起始地址与占用寄存器数
        （32位类型占2个寄存器，其余占1个），并统计寄存器总长度。
        """
        start_num = 0
        num = 0
        for_num = len(dic)
        for row in dic:
            if dic[row][3] in ['Real', 'real', 'dint', 'DInt', 'DWord', 'dword', 'Dword', 'Dint']:
                dic[row] += [start_num, 2, 0]
                start_num += 2
            else:
                dic[row] += [start_num, 1, 0]
                start_num += 1
            num += 1
            if num == for_num:
                mb_db_size = dic[row][-3] + dic[row][-2]
        return dic, mb_db_size

    njson = f'./snap7/{json_fname}.json'
    try:
        temp_data, iec104_data, record_data = convert_list(csv, 'gbk')
        WD_file(njson, temp_data)
        read_dic = copy.deepcopy(temp_data)
        rd_dic, mbk_size = add_mb_db(read_dic)
        WD_modbus_table(f'./snap7/{json_fname}_modbus点位表.csv', rd_dic, json_fname, id)
        dbmms = max_min_size(rd_dic)
    except Exception as err:
        public_lib.rich_info(LOGGER_FILE, 0, 'RD_file->', err)
    return rd_dic, mbk_size, dbmms, iec104_data, record_data

# ======================== webapi和mqtt公共工具 ========================
def pub_data(plcName, dic):
    """将点位数据组装为 {'PLC名@点位名': 点位值} 形式的字典，供MQTT/WebAPI上报使用。"""
    temp_dic = {}
    for tag in dic:
        temp_dic[f'{plcName}@{tag}'] = dic[tag][-1]
    return temp_dic

def pub_code_data(code_pub, data):
    """按用户配置的上报格式模板对数据做自定义转换：
    将模板中的 @@@ 占位符替换为实际数据字典后求值，并把 nan 替换为 0。
    """
    new_data = code_pub.replace('@@@', str(data)).replace('nan', '0')
    return eval(new_data)

def cmp_plc(set_data, data):
    """校验上位机下发的写指令：过滤掉本地不存在或不可写（非读写权限）的PLC与点位，
    防止无效写入引发异常。
    """
    set_data_plc = [plcName.split('@')[0]  for plcName in list(set_data.keys())]    # 下发plc列表
    plcName_list = [sgl_data[PLCParamIndex.PLC_NAME] for sgl_data in data]  # 本地plc列表
    res = set(set_data_plc) - set(plcName_list) # 找出不存在的PLC
    for notPlc in res:
        for key in  list(set_data.keys()):
            if notPlc in key:
                set_data.pop(key)   # 去掉不存在plc的键
    set_data_tag =  [plcName.split('@')[1]  for plcName in list(set_data.keys())]    # 下发标签列表
    tag_list = [list(sgl_data[PLCParamIndex.SNAP7_RW_POINT].keys()) for sgl_data in data] # 本地可写标签列表
    tag_list = [x for sub in tag_list for x in sub]     # 整合列表
    res = set(set_data_tag) - set(tag_list) # 找出不存在的标签
    for notTag in res:
        for key in list(set_data.keys()):
            if notTag in key:
                set_data.pop(key)   # 去掉不存在标签的键
    return set_data
# ======================== MQTT线程 ========================
def main_mqtt(topic, mqtt_en, Ton_Pub, code_pub):
    """MQTT服务线程：
    1) 按上报间隔(Ton_Pub)将所有PLC的点位数据发布到指定主题；
    2) 解析订阅收到的 SetProperty 写指令，校验后写入对应PLC（含重试机制）。
    """
    mqtt_t0 = 0  # 上报计时器（秒）
    Ton_Pub = int(Ton_Pub)
    # MQTT主循环：定时上报数据，并处理订阅收到的写指令
    while True:
        try:
            if ctx.program_exit_en == 1:
                break
            if mqtt_en == '1' and Mqtt_Server.client.connected:
                if mqtt_t0 >= Ton_Pub:
                    for sgl_data in ctx.data_all:
                        dic0 = copy.deepcopy(sgl_data[PLCParamIndex.SNAP7_POINT])
                        dic0['状态'] = [1 if sgl_data[PLCParamIndex.PLC_STATE] else 0]
                        try:
                            convert_data = pub_code_data(code_pub, pub_data(sgl_data[PLCParamIndex.PLC_NAME], dic0))
                            temp_data = json.dumps(convert_data, indent=1, ensure_ascii=False)
                            Mqtt_Server.client._publish_message(topic, temp_data)
                        except Exception as err:
                            err_data = json.dumps({'Error': '格式转换出错！'}, indent=1, ensure_ascii=False)
                            Mqtt_Server.client._publish_message(topic, err_data)
                            public_lib.rich_info(LOGGER_FILE, 0, '上报数据转换出错->', f'{sgl_data[PLCParamIndex.PLC_NAME]}-{err}')
                    mqtt_t0 = 0
                if Mqtt_Server.client.global_message:
                    try:
                        rec_data = json.loads(Mqtt_Server.client.global_message)
                        set_data = rec_data['SetProperty']
                        retry_count = {key: 0 for key in set_data}
                        set_data = cmp_plc(set_data, ctx.data_all)  # 去除不存在的plc键
                        while set_data:
                            for k, sgl_data in enumerate(ctx.data_all):
                                
                                comp_dic = sgl_data[PLCParamIndex.SNAP7_RW_POINT]
                                for i in list(set_data.keys()):
                                    plcname, tag = i.split('@')
                                    try:
                                        if (tag in comp_dic 
                                            and plcname == sgl_data[PLCParamIndex.PLC_NAME] 
                                            and sgl_data[PLCParamIndex.PLC_STATE] == 1
                                            and not ctx.client_plc.params[k][PLCParamIndex.PLC_READ_EN]):
                                            ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 1
                                            data_type = comp_dic[tag][3]
                                            raw_val = set_data[i]
                                            if data_type in ['int', 'Int']:
                                                temp = DataConvertUtil.int16_to_uint(raw_val)
                                            elif data_type in ['dint', 'DInt']:
                                                temp = DataConvertUtil.int32_to_uint32(raw_val)
                                            elif data_type in ['byte', 'Byte']:
                                                temp = DataConvertUtil.byte_to_ubyte(raw_val)
                                            else:
                                                temp = raw_val
                                            ctx.client_plc.set(
                                                plcName=plcname,
                                                db_num=comp_dic[tag][0],
                                                start_addr=comp_dic[tag][1],
                                                data=temp,
                                                dtype=comp_dic[tag][3],
                                                device=ctx.client_plc.params[k][0]
                                            )
                                            data_display = DataConvertUtil.byte_to_ubyte(raw_val) if data_type in ['byte', 'Byte'] else raw_val
                                            dtypePrint = f'DB块：{comp_dic[tag][0]}' if comp_dic[tag][0] != 0 else 'MB块'
                                            wd = f'{dtypePrint}，点位名称：{tag}，起始地址：{comp_dic[tag][1]}，写入数据：{data_display}，数据类型：{data_type}'
                                            public_lib.rich_info(WRITE_LOG_FILE, 1, f'通过MQTT写入{plcname}', wd)
                                            set_data.pop(i)
                                        elif tag in comp_dic and plcname == sgl_data[PLCParamIndex.PLC_NAME] and sgl_data[PLCParamIndex.PLC_STATE] == 0:
                                            set_data.pop(i)
                                    except Exception as err:
                                        err_str = str(err)
                                        if "Job pending" in err_str or "Connection timed out" in err_str:
                                            retry_count[i] += 1
                                            time.sleep(0.5)
                                            if retry_count[i] > 6:
                                                public_lib.rich_info(LOGGER_FILE, 0, f'设备:{plcname},点位名称：{tag}写数据到设备失败->', err)
                                                set_data.pop(i)
                                        else:
                                            set_data.pop(i)
                                            public_lib.rich_info(LOGGER_FILE, 0, 'MQTT写数据到设备出错->', err)
                                ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 0
                                    
                        else:
                            Mqtt_Server.client.global_message = ''
                    except Exception as err:
                        pass
                    # Mqtt_Server.client.global_message = ''
        except Exception as err:
            Mqtt_Server.client.global_message = ''
            public_lib.rich_info(LOGGER_FILE, 0, 'MQTT接收或发送出错->', err)
        mqtt_t0 += 1
        time.sleep(1)

# ======================== WebAPI线程 ========================
def main_webapi(freq=1):
    """WebAPI服务线程：
    1) 实时将各PLC最新点位数据更新至WebAPI响应字典；
    2) 处理HTTP接口收到的 SetProperty 写请求，校验后写入PLC。
    """
    mode = 0
    # 根据上报格式模板确定响应数据的外层键名，模板不可用时退化为平铺模式(mode=1)
    try:
        key_pub = [key for key in pub_code_data(ctx.code_pub, '1').keys()][0]
        ctx.server_web_api.send_data[key_pub] = {}
        mode = 0
    except:
        ctx.server_web_api.send_data = {}
        mode = 1
    # WebAPI主循环：实时更新响应数据，并处理HTTP下发的写请求
    while True:
        if ctx.web_api_exit_en == 1:
            ctx.server_web_api.stop()
            break
        for sgl_data in ctx.data_all:
            dic0 = copy.deepcopy(sgl_data[PLCParamIndex.SNAP7_POINT])
            dic0['状态'] = [1 if sgl_data[PLCParamIndex.PLC_STATE] else 0]
            try:
                convert_data = pub_code_data(ctx.code_pub, pub_data(sgl_data[PLCParamIndex.PLC_NAME], dic0))
                if mode == 0:
                    ctx.server_web_api.send_data[key_pub].update(convert_data[key_pub])
                else:
                    ctx.server_web_api.send_data.update(convert_data)
            except Exception as err:
                public_lib.rich_info(LOGGER_FILE, 0, 'webapi数据转换出错->', f'{sgl_data[PLCParamIndex.PLC_NAME]}-{err}')
        if ctx.server_web_api.received_flag:
            ctx.server_web_api.received_flag = False
            try:
                rec_data = ctx.server_web_api.received_data
                set_data = rec_data['SetProperty']
                set_data = cmp_plc(set_data, ctx.data_all)  # 去除不存在的plc键
                while set_data:
                    for k, sgl_data in enumerate(ctx.data_all):
                        comp_dic = sgl_data[PLCParamIndex.SNAP7_RW_POINT]   # 下发标签列表
                        plcname = sgl_data[PLCParamIndex.PLC_NAME]          # 下发plc名
                        
                        for i in list(set_data.keys()):
                            target_plc, tag = i.split('@')
                            if  (not sgl_data[PLCParamIndex.PLC_STATE] and target_plc == plcname):
                                set_data.pop(i)
                                continue
                            if target_plc != plcname or tag not in comp_dic or ctx.client_plc.params[k][PLCParamIndex.PLC_READ_EN] :
                                continue
                            ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 1
                            data_type = comp_dic[tag][3]
                            raw_val = set_data[i]
                            if data_type in ['int', 'Int']:
                                temp = DataConvertUtil.int16_to_uint(raw_val)
                            elif data_type in ['dint', 'DInt']:
                                temp = DataConvertUtil.int32_to_uint32(raw_val)
                            elif data_type in ['byte', 'Byte']:
                                temp = DataConvertUtil.byte_to_ubyte(raw_val)
                            else:
                                temp = raw_val
                            ctx.client_plc.set(
                                plcName=plcname,
                                db_num=comp_dic[tag][0],
                                start_addr=comp_dic[tag][1],
                                data=temp,
                                dtype=comp_dic[tag][3],
                                device=ctx.client_plc.params[k][0]
                            )
                            data_display = DataConvertUtil.byte_to_ubyte(raw_val) if data_type in ['byte', 'Byte'] else raw_val
                            dtypePrint = f'DB块：{comp_dic[tag][0]}' if comp_dic[tag][0] != 0 else 'MB块'
                            wd = f'{dtypePrint}，点位名称：{tag}，起始地址：{comp_dic[tag][1]}，写入数据：{data_display}，数据类型：{data_type}'
                            public_lib.rich_info(WRITE_LOG_FILE, 1, f'通过webApi写入{plcname}', wd)
                            set_data.pop(i)
                        ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 0
                else:
                    ctx.server_web_api.received_data.clear()
            except Exception as err:
                ctx.server_web_api.received_data.clear()
                public_lib.rich_info(LOGGER_FILE, 0, 'webApi写数据到设备出错->', err)
            
        time.sleep(freq)

# ======================== ModbusTCP线程 ========================
def main_modbus(ip, port, params, rd_freq=1):
    """Modbus TCP服务端线程：
    1) 将最新采集的S7点位数据同步到对应站号的保持寄存器块；
    2) 消费Modbus主站的写请求队列(wd_list)，按点位长度做类型转换后写入PLC。
    """
    tmp_dict = {}
    # 按各设备的Modbus站号初始化保持寄存器块（初值全0）
    for sgl_param in params:
        tmp_dict[sgl_param[PLCParamIndex.MODBUS_ID]] = [0 for _ in range(sgl_param[PLCParamIndex.MODBUS_DB_SIZE])]
    MBSlave = Modbus_Server.ModbusServer(host=ip, port=port, blocks=tmp_dict)
    WD_EN = 1
    TIEM_OUT_NUM =4
    modbus_WDErr_num = 0 #写次数
    # Modbus主循环：同步采集数据到寄存器块，并消费主站写请求队列
    while True:
        if ctx.program_exit_en == 1:
            MBSlave.stop_flag = 1
            break
        try:
            if ctx.mbtcp_en == '1':
                if modbus_WDErr_num >= 3 or len(MBSlave.wd_list) == 0:
                    # 写入3次都不成功，清空写入列表
                    modbus_WDErr_num = 0
                    MBSlave.wd_list = []
                for k, sgl_data in enumerate(ctx.data_all):
                    # 设备在线超时计数初始化
                    if sgl_data[PLCParamIndex.PLC_STATE]:
                        sgl_data[PLCParamIndex.MODBUS_TMP][TIEM_OUT_NUM] = 0.0  
                    tmp_list = MBSlave.wd_list.copy()   # 拷贝下发点表
                    for tag in sgl_data[PLCParamIndex.SNAP7_RW_POINT]:
                        tag_list = sgl_data[PLCParamIndex.SNAP7_RW_POINT][tag]
                        try:
                            for i in tmp_list:
                                if (i[0] == sgl_data[PLCParamIndex.MODBUS_ID] 
                                    
                                    and tag_list[6] == i[1] 
                                    and not ctx.client_plc.params[k][PLCParamIndex.PLC_READ_ERR]
                                    and not ctx.client_plc.params[k][PLCParamIndex.PLC_READ_EN]
                                    ):
                                    ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 1
                                    if tag_list[7] == 1 and type(i[2]) == int:
                                        tmp_data = i[2]
                                        ctx.client_plc.set(
                                            plcName=sgl_data[PLCParamIndex.PLC_NAME],
                                            db_num=tag_list[0],
                                            start_addr=tag_list[1],
                                            data=tmp_data,
                                            dtype=tag_list[3],
                                            device=ctx.client_plc.params[k][0]
                                        )
                                        MBSlave.wd_list.remove(i)   #写成功后移除待写入点位
                                    if tag_list[7] == 2 and type(i[2]) == list:
                                        if tag_list[3] in ['real', 'Real']:
                                            tmp_data = DataConvertUtil.two_word_to_float(i[2][0], i[2][1])
                                        if tag_list[3] in ['dword', 'DWord', 'dint', 'DInt']:
                                            tmp_data = (i[2][0] << 16) | i[2][1]
                                        ctx.client_plc.set(
                                            plcName=sgl_data[PLCParamIndex.PLC_NAME],
                                            db_num=tag_list[0],
                                            start_addr=tag_list[1],
                                            data=tmp_data,
                                            dtype=tag_list[3],
                                            device=ctx.client_plc.params[k][0]
                                        )
                                        MBSlave.wd_list.remove(i)   #写成功后移除待写入点位
                                        ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 0
                                        data_display = DataConvertUtil.uint32_to_int32(tmp_data) if tag_list[3] in ['dint', 'DInt'] else tmp_data
                                    if 1: 
                                        # 显示值处理
                                        if  tag_list[3] in ['bool', 'Bool', 'int', 'Int', 'byte', 'Byte'] :
                                            data_display = DataConvertUtil.uint_to_int16(tmp_data)
                                        elif tag_list[3] in ['byte', 'Byte'] :
                                            data_display = DataConvertUtil.int16_to_uint(tmp_data) 
                                        else:
                                            data_display = tmp_data
                                        dtypePrint = f'DB块：{tag_list[0]}' if tag_list[0] != 0 else 'MB块'
                                        wd = f'{dtypePrint}，点位名称：{tag}，起始地址：{tag_list[1]}，写入数据：{data_display}，数据类型：{tag_list[3]}'
                                        public_lib.rich_info(WRITE_LOG_FILE, 1, f'通过Modbus写入{sgl_data[PLCParamIndex.PLC_NAME]}', wd)
                                elif i[0] == sgl_data[PLCParamIndex.MODBUS_ID] and tag_list[6] == i[1] and ctx.client_plc.params[k][PLCParamIndex.PLC_READ_ERR] :
                                    # PLC超时30秒，清空下发列表
                                    sgl_data[PLCParamIndex.MODBUS_TMP][TIEM_OUT_NUM] += rd_freq * 0.5
                                    if sgl_data[PLCParamIndex.MODBUS_TMP][TIEM_OUT_NUM] >= 30.0:
                                        public_lib.rich_info(LOGGER_FILE, 1,
                                                            f'{sgl_data[PLCParamIndex.PLC_NAME]}', 
                                                            f'超时后写数据->ID:{i[0]},点位{i[1]},值{i[2]}')
                                        MBSlave.wd_list.remove(i)   #写成功后移除待写入点位
                                        sgl_data[PLCParamIndex.MODBUS_TMP][TIEM_OUT_NUM] = 0.0
                                        
                            ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 0
                        except Exception as err:
                            ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 0
                            # public_lib.rich_info(LOGGER_FILE, 0, 'main_modbus写出错->', err)

                    # 数据显示更新判断
                    if sgl_data[PLCParamIndex.MODBUS_POINT] != sgl_data[PLCParamIndex.MODBUS_LAST_POINT] :
                        MBSlave.blocks[sgl_data[PLCParamIndex.MODBUS_ID]] = sgl_data[PLCParamIndex.MODBUS_POINT]  
                        sgl_data[PLCParamIndex.MODBUS_LAST_POINT] =  sgl_data[PLCParamIndex.MODBUS_POINT].copy()
        except Exception as err:
            modbus_WDErr_num += 1
            err_str = str(err)
            if "b'CLI : Job pending'" not in err_str and "Connection timed out" not in err_str:
                public_lib.rich_info(LOGGER_FILE, 0, 'main_modbus出错->', err)
        time.sleep(rd_freq * 0.5)

# ======================== IEC104线程 ========================
def main_iec104(freq=0.5):
    """IEC104服务线程：
    1) 将采集的点位数据按类型整理为浮点遥测/整型遥测/单点遥信并刷新到IEC104服务端；
    2) 消费主站下发的遥控命令(download_list)，类型转换后写入PLC。
    """
    TIEM_OUT_NUM = 0
    IEC104_IOA = 5
    type_value = {50: 'real', 49: 'int', 45: 'bool'}
    iec_104_WDErr_num = 0 #写次数
    last_err_str = ''
    # IEC104主循环：刷新遥测/遥信数据，并处理主站下发的遥控命令
    while True:
        if ctx.iec104_server.stop_flag:
            break
        iec104_data_dict = {i: [] for i in [0x0d, 0x0b, 0x01]}
        if iec_104_WDErr_num >= 3 or len(ctx.iec104_server.download_list) == 0:
            # 写入3次都不成功，清空写入列表
            iec_104_WDErr_num = 0
            ctx.iec104_server.download_list = []
        for sgl_data in ctx.data_all:
            for key in sgl_data[PLCParamIndex.SNAP7_POINT]:
                try:
                    # [1, 9, 1, 'byte', '读写', 0, 0, 1, 0]
                    row = sgl_data[PLCParamIndex.SNAP7_POINT][key]
                    ioa = row[IEC104_IOA]
                    value = row[-1]
                    type_id = 0
                    wd_id = 0
                    tag = key
                    if row[3] in ['real', 'Real']:
                        type_id = 0x0d
                        wd_id = 50
                    elif row[3] in ['int', 'Int']:
                        type_id = 0x0b
                        wd_id = 49
                    elif row[3] in ['bool', 'Bool']:
                        type_id = 0x01
                        wd_id = 45
                    if ioa != 0:
                        iec104_data_dict[type_id].append([ioa, value, type_id, wd_id, tag])
                except Exception:
                    pass
        ctx.iec104_server.data_real = iec104_data_dict[0x0d]
        ctx.iec104_server.data_int = iec104_data_dict[0x0b]
        ctx.iec104_server.data_bool = iec104_data_dict[0x01]
        dwon_list = ctx.iec104_server.download_list.copy()  # 保存待写入数据
        for iec104_wd in dwon_list:
            if type(iec104_wd[0]) == int:
                iec104_wd[0] = type_value[iec104_wd[0]]
            for k, sgl_data in enumerate(ctx.data_all):
                comp_dic = sgl_data[PLCParamIndex.SNAP7_RW_POINT]
                plcname = sgl_data[PLCParamIndex.PLC_NAME]
                try:
                    for key in comp_dic:
                        if (comp_dic[key][IEC104_IOA] == iec104_wd[1] 
                            and not ctx.client_plc.params[k][PLCParamIndex.PLC_READ_ERR] 
                            and sgl_data[PLCParamIndex.PLC_STATE]
                            and not ctx.client_plc.params[k][PLCParamIndex.PLC_READ_EN]
                            ):
                            ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 1
                            data_type = comp_dic[key][3]
                            raw_val = iec104_wd[2]
                            if data_type in ['int', 'Int']:
                                temp_wd = DataConvertUtil.int16_to_uint(raw_val)
                            elif data_type in ['dint', 'DInt']:
                                temp_wd = DataConvertUtil.int32_to_uint32(raw_val)
                            elif data_type in ['byte', 'Byte']:
                                temp_wd = DataConvertUtil.byte_to_ubyte(raw_val)
                            else:
                                temp_wd = raw_val
                            ctx.client_plc.set(
                                plcName=plcname,
                                db_num=comp_dic[key][0],
                                start_addr=comp_dic[key][1],
                                data=temp_wd,
                                dtype=comp_dic[key][3],
                                device=ctx.client_plc.params[k][0]
                            )
                            ctx.iec104_server.download_list.remove(iec104_wd)
                            data_display = DataConvertUtil.uint_to_int16(temp_wd) if data_type in ['int', 'Int'] else temp_wd
                            dtypePrint = f'DB块：{comp_dic[key][0]}' if comp_dic[key][0] != 0 else 'MB块'
                            wd = f'{dtypePrint}，点位名称：{key}，起始地址：{comp_dic[key][1]}，写入数据：{data_display}，数据类型：{comp_dic[key][3]}'
                            public_lib.rich_info(WRITE_LOG_FILE, 1, f'通过IEC104写入{plcname}', wd)
                            last_err_str = ''
                        elif comp_dic[key][IEC104_IOA] == iec104_wd[1] and sgl_data[PLCParamIndex.PLC_READ_ERR]:
                             # PLC超时30秒，清空下发列表
                            sgl_data[PLCParamIndex.IEC104_TMP][TIEM_OUT_NUM] += freq * 0.5
                            if sgl_data[PLCParamIndex.IEC104_TMP][TIEM_OUT_NUM] >= 30.0:
                                public_lib.rich_info(LOGGER_FILE, 1,
                                                        f'{sgl_data[PLCParamIndex.PLC_NAME]}', 
                                                        f'超时后写数据->IOA:{iec104_wd[1]},数据类型:{iec104_wd[0]},值{iec104_wd[2]}')
                                ctx.iec104_server.download_list.remove(iec104_wd)   #写成功后移除待写入点位
                                sgl_data[PLCParamIndex.MODBUS_TMP][TIEM_OUT_NUM] = 0.0
                    ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 0
                    
                except Exception as err:
                    ctx.client_plc.params[k][PLCParamIndex.SNAP7_WRITE_EN] = 0
                    # iec_104_WDErr_num += 1
                    err_str = str(err)
                    if "Job pending" not in err_str and "Connection timed out" not in err_str and err_str != last_err_str:
                        last_err_str = err_str
                        public_lib.rich_info(LOGGER_FILE, 0, 'IEC104写数据到设备出错->', err)
        # ctx.iec104_server.download_list = []
        time.sleep(freq * 0.5)

# ======================== 归档线程 ========================
def record_thread():
    """数据归档线程：按归档周期将各在线PLC所有归档字段的最新值写入SQLite数据库。"""
    if ctx.record_cycle >= 60 :
        Sqlite_OP.db_mgr.cache_flush_sec = ctx.record_cycle
    time.sleep(ctx.param_freq*2)
    # 归档主循环：按归档周期将各PLC最新数据写入SQLite
    while True:
        if ctx.program_exit_en == 1:
            break
        if ctx.record_en == '1' :
            tmp_dict = {}
            for sgl_data in ctx.data_all:
            # 遍历所有表
                for table_name, fields in sgl_data[PLCParamIndex.RECORD_DATA].items():
                    # 遍历当前表内所有字段名
                    for field in fields:
                        # b[字段][-1] 取列表最后一位数值
                        try:
                            sgl_data[PLCParamIndex.RECORD_DATA][table_name][field] = sgl_data[PLCParamIndex.SNAP7_POINT][field][-1]
                        except Exception as err:
                            pass
                if sgl_data[PLCParamIndex.PLC_STATE] :
                    tmp_dict[sgl_data[PLCParamIndex.PLC_NAME]] = sgl_data[PLCParamIndex.RECORD_DATA]
            Sqlite_OP.record(Sqlite_OP.SQL_PATH, tmp_dict)
        time.sleep(ctx.record_cycle)

# ======================== 主线程main ========================
def main(params, rd_freq=1):
    """采集主线程：
    1) 启动Snap7Client采集与连接维护线程；
    2) 按配置依次启动Modbus/MQTT/WebAPI/IEC104/归档服务线程；
    3) 主循环中把采集到的原始数据解码为点位值、刷新Modbus点表，并同步至ctx.data_all供各服务使用。
    """
    ctx.data_all = copy.deepcopy(params)
    ctx.client_plc = Snap7Client(params, rd_freq=rd_freq)
    # 启动PLC连接维护与采集线程
    thd_main = thd.Thread(target=ctx.client_plc.main)
    thd_main.daemon = True
    thd_main.start()
    if ctx.mbtcp_en == '1':
        thd0 = thd.Thread(target=main_modbus, args=(ctx.mb_ip, int(ctx.mb_port), params, rd_freq), daemon=True)
        thd0.start()
    if ctx.mqtt_en == '1':
        thd1 = thd.Thread(target=main_mqtt, args=(ctx.topic, ctx.mqtt_en, ctx.Ton_Pub, ctx.code_pub), daemon=True)
        thd1.start()
    if ctx.web_api_en == '1':
        thd2 = thd.Thread(target=main_webapi, daemon=True)
        thd2.start()
    if ctx.iec104_en == '1':
        thd3 = thd.Thread(target=main_iec104, daemon=True)
        thd3.start()
    if ctx.record_en == '1':
        thd4 = thd.Thread(target=record_thread,  daemon=True)
        thd4.start()
    last_err = ''
    while True:
        try:
            num = 0
            tmp_en = 0
            for sgl_param in ctx.client_plc.params:
                if (sgl_param[PLCParamIndex.PLC_STATE] 
                    and len(sgl_param[PLCParamIndex.SNAP7_POINT]) != 0 
                    and len(sgl_param[PLCParamIndex.SNAP7_RD_TMP]) != 0 
                    and sgl_param[PLCParamIndex.SNAP7_READ_SUCCESS] == 1
                    ):
                    for j in sgl_param[PLCParamIndex.SNAP7_POINT]:
                        dic_value = sgl_param[PLCParamIndex.SNAP7_POINT][j]
                        try:
                            plc_name = sgl_param[PLCParamIndex.PLC_NAME]
                            db_num = dic_value[0]
                            start_addr = dic_value[1]
                            size = dic_value[2]
                            dataType = dic_value[3]
                            dic_value[8] = ctx.client_plc.get(plc_name, db_num, start_addr, size, dataType)
                            tmp_en = 1
                        except Exception as err:
                            str_err = str(err)
                            if sgl_param[PLCParamIndex.SNAP7_READ_ERR] != 1 and str_err != last_err:
                                public_lib.rich_info(LOGGER_FILE, 0, f'main_snap7[{sgl_param[PLCParamIndex.PLC_NAME]}]->', err)
                                tmp_en = 0
                            last_err = str_err
                            time.sleep(0.1)
                            try:
                                if not sgl_param[PLCParamIndex.INSTANCE].get_connected():
                                    sgl_param[PLCParamIndex.PLC_STATE] = 0
                                    break
                            except Exception:
                                sgl_param[PLCParamIndex.PLC_STATE] = 0
                    last_err = ''
                    sgl_param[PLCParamIndex.MODBUS_POINT] = s7_to_mb(sgl_param[PLCParamIndex.SNAP7_POINT])
                    sgl_param[PLCParamIndex.SNAP7_READ_SUCCESS] = 0
                else:
                    tmp_en = 0
                ctx.data_all[num][1:10] = copy.deepcopy(sgl_param[1:10])
                if tmp_en == 1:
                    ctx.data_all[num][PLCParamIndex.SNAP7_READ_EN] = 1
                num += 1
            if ctx.program_exit_en == 1:
                ctx.client_plc.exit = 1
                ctx.program_state_exit = 1
                break
        except Exception as err:
            public_lib.rich_info(LOGGER_FILE, 0, 'Main_Sanp7_0->', err)
            ctx.data_all[num][PLCParamIndex.SNAP7_READ_EN] = 0
            time.sleep(1)
        time.sleep(rd_freq)

# ======================== 配置加载初始化函数 ========================
def fc_parameter():
    """配置加载与初始化函数：
    1) 读取config.ini中的全部配置项（Modbus/MQTT/WebAPI/IEC104/归档/数据查询服务等）；
    2) 解析各PLC参数并读取点位表CSV，构建完整参数列表；
    3) 创建运行目录与日志文件，初始化各协议服务实例，最后调用main()启动采集。
    """
    try:
        # 从config.ini读取全部协议与服务配置项
        read_config = public_lib.cfg_read('config.ini', 'UTF-8')
        ctx.mb_ip = read_config['mbtcp地址']
        ctx.mb_port = int(read_config['mbtcp端口'])
        ctx.param_freq = float(read_config['读取频率'])
        ctx.Ton_Pub = int(read_config['上报间隔'])
        ctx.topic = str(read_config['发布主题'])
        ctx.mss_topic = str(read_config['订阅主题'])
        ctx.mqtt_en = read_config['MQTT使能']
        ctx.mbtcp_en = read_config['mbtcp使能']
        ctx.plc_para = eval(read_config['控制器参数'])
        ctx.code_pub = read_config['上报格式模板']
        ctx.autorun_en = int(read_config['开机启动'])
        ctx.delay_arun = int(read_config['自启延时'])
        ctx.dir_hide = int(read_config['隐藏文件夹'])
        ctx.topic_ft = f"{ctx.topic}/file_transfer"
        ctx.topic_fb = f'{ctx.topic}/faceback'
        ctx.webApi_ip = read_config['webApi地址']
        ctx.webApi_port = int(read_config['webApi端口'])
        ctx.web_api_en = read_config['webApi使能']
        ctx.iec104_en = read_config['iec104使能']
        ctx.iec104_addr = read_config['iec104地址']
        ctx.iec104_port = int(read_config['iec104端口'])
        ctx.iec104_coa = int(read_config['iec104站地址'])
        ctx.record_en = read_config['归档使能']
        ctx.record_cycle = int(read_config['归档周期'])
        ctx.query_data_en = read_config['数据查询服务使能']
        ctx.query_data_ip = read_config['数据查询服务地址']
        ctx.query_data_port = int(read_config['数据查询服务端口'])
    except Exception as err:
        public_lib.rich_info(LOGGER_FILE, 0, '读取配置文件出错->', err)
    try:
        para_defaul = ['s7实例名称','127.0.0.1',1, 'plc1',10,[],{},0,0,{},{},{},{},0,0,0,0,
                             0, '机架号', '槽号', 'iec104点位', [0.0, False, False, False,0.0], [0.0,False],'归档数据',
                             0,     # s7写使能
                             0,     # s7读取失败计数
                             [],    # 上一modbus点表
                             0,     # s7读取错误标识
                             False  # 数据块读取标识
                             ]
        ctx.temp_para = [copy.deepcopy(para_defaul) for _ in range(len(ctx.plc_para))]
        tmp_params = []
        for num in range(len(ctx.temp_para)):
            plc_cfg = ctx.plc_para[num]
            ctx.temp_para[num][PLCParamIndex.DEVICE_IP] = plc_cfg[0]
            ctx.temp_para[num][PLCParamIndex.MODBUS_ID] = plc_cfg[1]
            ctx.temp_para[num][PLCParamIndex.PLC_NAME] = plc_cfg[2]
            ctx.temp_para[num][PLCParamIndex.PLC_RACK] = plc_cfg[4]
            ctx.temp_para[num][PLCParamIndex.PLC_SLOT] = plc_cfg[5]
            csv_file = plc_cfg[3]
            plc_modbus_id = plc_cfg[1]
            plc_name_tag = ctx.temp_para[num][PLCParamIndex.PLC_NAME]
            dic, mb_db_size, db_max_min_size, iec104_data, record_data = RD_file(csv_file, plc_name_tag, plc_modbus_id)
            ctx.temp_para[num][PLCParamIndex.MODBUS_DB_SIZE] = mb_db_size
            ctx.temp_para[num][PLCParamIndex.SNAP7_POINT] = dic
            temp_comp_dic = copy.deepcopy(dic)
            comp_dic = {k: temp_comp_dic[k] for k in temp_comp_dic if temp_comp_dic[k][4] == '读写'}
            ctx.temp_para[num][PLCParamIndex.SNAP7_RW_POINT] = comp_dic
            ctx.temp_para[num][PLCParamIndex.DB_MAN_MIN] = db_max_min_size
            ctx.temp_para[num][PLCParamIndex.IEC104_POINT] = iec104_data
            ctx.temp_para[num][PLCParamIndex.RECORD_DATA] = record_data
            tmp_params.append(copy.deepcopy(ctx.temp_para[num]))
        ctx.params = tmp_params
    except Exception as err:
        public_lib.rich_info(LOGGER_FILE, 0, '启动时请勿使用其他程序打开生成的文件->', err)
    ctx.dir_hide = 1 if (ctx.dir_hide > 0) else 0
    public_lib.mk_folder('snap7', hide=ctx.dir_hide)
    public_lib.mk_folder('TMP', hide=ctx.dir_hide)
    public_lib.exists_file(LOGGER_FILE, f'{title_text2}\n{myinfo}\n')
    if public_lib.system_type == 'nt':
        try:
            public_lib.autorun_snap7(int(ctx.autorun_en), LOGGER_FILE)
        except Exception as err:
            public_lib.rich_info(LOGGER_FILE, 0, 'autorun_snap7->', err)
    # MQTT
    if ctx.mqtt_en == '1':
        Mqtt_Server.mqtt_main()
        Mqtt_Server.client.logger_file = LOGGER_FILE
        Mqtt_Server.client.transfer_log = "./sanp7/log_MQTT_transfer.txt"
    # Web Api
    if ctx.web_api_en == '1':
        ctx.server_web_api = web_api.WebAPI(ip=ctx.webApi_ip, port=ctx.webApi_port)
        ctx.server_web_api.log_path = LOGGER_FILE
        ctx.server_web_api.start()
    # IEC104
    if ctx.iec104_en == '1':
        iec104_data_dict = {i: [] for i in [0x0d, 0x0b, 0x01]}
        for p in ctx.params:
            tmp_data = p[PLCParamIndex.IEC104_POINT]
            for code in [0x0d, 0x0b, 0x01]:
                iec104_data_dict[code].extend(tmp_data[code])
        iec104_Server.IEC104Config.LOGGER_FILE = LOGGER_FILE
        iec104_Server.IEC104Config.COA_ADDR = ctx.iec104_coa
        ctx.iec104_server = iec104_Server.IEC104Server(
            ctx.iec104_addr,
            ctx.iec104_port,
            data_real=iec104_data_dict[0x0d],
            data_int=iec104_data_dict[0x0b],
            data_bool=iec104_data_dict[0x01]
        )
        thd11 = thd.Thread(target=ctx.iec104_server.run, daemon=True)
        thd11.start()
    # 数据库浏览服务
    if ctx.query_data_en == '1':
        try:
            ctx.data_viewer_server = data_viewer.DataWebServer(ip=ctx.query_data_ip,port=ctx.query_data_port)
            ctx.data_viewer_server.LOGGER_FILE = LOGGER_FILE
            ctx.data_viewer_server.start()
            
        except Exception as err:
            public_lib.rich_info(LOGGER_FILE, 0, '启动数据查询服务失败->', err)
    main(ctx.params, ctx.param_freq / 1)

# 程序启动入口
def start_up():
    """程序启动入口：以异步线程方式执行配置加载与初始化函数fc_parameter。"""
    thd20 = thd.Thread(target=fc_parameter, daemon=1)
    thd20.start()

# ======================== 程序入口 ========================
if __name__ == '__main__':
    # 定义启动横幅与作者信息，创建运行目录和日志文件
    title_text = '''                                                                       
▄▄  ▄▄▄▄  ▄▄▄▄ ▄▄▄▄▄▄ ▄▄▄▄▄  ▄▄▄  ▄▄   ▄▄    ▄▄▄▄ ▄▄  ▄▄ 
██ ██▀▀▀ ███▄▄   ██   ██▄▄  ██▀██ ██▀▄▀██   ██▀▀▀ ███▄██ 
██ ▀████ ▄▄██▀   ██   ██▄▄▄ ██▀██ ██   ██ ▄ ▀████ ██ ▀██ 
                                       
'''
    title_text2 = '''        
                icsteam.cn                                   
        '''
    myinfo = f'作者：工控万金油\n版本：{VERSION}\n首发日期：2024年05月18日\n更新日期：{UPDATE}\n\
仓库地址：https://gitee.com/icsteam/siemens-s7-convert\n邮箱：i@icssteam.cn\n声明：使用snap7、paho_mqtt、flask、rich等开源库\n\
📢 [red]使用Ctrl+C结束程序[/red]'
    log_myinfo = f'作者：工控万金油\n版本：{VERSION}\n首发日期：2024年05月18日\n更新日期：{UPDATE}\n\
仓库地址：https://gitee.com/icsteam/siemens-s7-convert\n邮箱：i@icssteam.cn\n声明：使用snap7、paho_mqtt、flask、rich等开源库'
    public_lib.set_top_message(title_text+myinfo)   # 打印程序信息
    public_lib.mk_folder('snap7')                   # 新建snap7文件夹
    public_lib.mk_folder('TMP')                     # 新建TMP文件夹
    public_lib.exists_file(LOGGER_FILE, f'{title_text2}\n{log_myinfo}\n')   #新建日志文件，并输入头部说明
    start_up()  # 启动程序
    try:
        while True:
            try:
                if Mqtt_Server.client is not None and Mqtt_Server.client.program_restart == 1:
                    ctx.program_exit_en = 1
                    if ctx.iec104_server:
                        ctx.iec104_server.stop_flag = True
                    ctx.web_api_exit_en = 1
                    Mqtt_Server.client.mqtt_exit = 1
                    if ctx.query_data_en == '1':
                        ctx.data_viewer_server.stop()
                    public_lib.rich_info(LOGGER_FILE, 1, '记录', '程序重启中！')
                    time.sleep(5)
                    if ctx.program_state_exit == 1:
                        ctx.program_state_exit = 0
                        ctx.program_exit_en = 0
                        ctx.web_api_exit_en = 0
                        Mqtt_Server.client.program_restart = 0
                        Mqtt_Server.client.mqtt_exit = 0
                        time.sleep(1)
                        start_up()
                time.sleep(2)
            except Exception as err:
                if Mqtt_Server.client is not None and Mqtt_Server.client.program_restart == 1:
                    Mqtt_Server.client._publish_message(Mqtt_Server.client.topic_feedback, json.dumps({"restart_err": str(err)}))
                time.sleep(5)
    except Exception as err:
        public_lib.rich_info(LOGGER_FILE, 1, '记录', '程序停止中！')
    finally:
        public_lib.close_live()
        Sqlite_OP.db_mgr.close_all()