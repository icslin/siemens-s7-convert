import selectors
import socket
import copy
import types
import struct
import time
import public_lib
from threading import Timer, Thread
from typing import Dict, List, Tuple, Optional, Any

# 配置与常量定义
class IEC104Config:
    """IEC 104协议服务器配置常量"""
    HOST = '0.0.0.0'
    PORT = 2404
    COA_ADDR = 1         # 站地址
    MAX_FRAME_REAL = 30  # 实数信息最大帧长度
    MAX_FRAME_INT = 40   # 整数信息最大帧长度
    MAX_FRAME_COIL = 60  # 线圈信息最大帧长度
    IOA_INDEX = 0        # IOA地址索引
    VALUE_INDEX = 1      # 值索引
    MAX_CONNECTIONS = 10  # 最大连接数
    COMMAND_SET = [45,46,47,48,49,50,51,58,59,60,61,62,63,64,110,111,112,113,
                   101,102,103,104,105,106,105,0x7A]
    # 类型ID常量
    TYPE_ID_REAL = 0x0d    # 浮点数
    TYPE_ID_INT = 0x0b     # 有符号整数
    TYPE_ID_BOOL = 0x01    # 布尔值
    # 传输原因常量
    COT_GENERAL_CALL = 0x14  # 总召唤
    COT_CHANGE = 0x03        # 变化上报
    COT_CONFIRM = 0x07       # 确认
    COT_STOP = 0x0A          # 停止
    COT_UNKNOWN = 0x6C       # 未知
    # 日志配置
    LOGGER_FILE = 'iec104_Server_log.txt'

class IEC104Server:
    """IEC 104协议服务器实现"""
    
    def __init__(self, host: str = IEC104Config.HOST, 
                 port: int = IEC104Config.PORT,
                 data_real: List = None,
                 data_int: List = None,
                 data_bool: List = None):
        """初始化服务器"""
        self.selector = selectors.DefaultSelector()
        self.clients: Dict[socket.socket, types.SimpleNamespace] = {}  # 客户端连接字典
        self.broadcast_timer: Optional[Timer] = None    # 定时广播定时器
        self.general_call_msg: Dict = {}                # 总召唤消息
        self.change_report_msg: Dict = {}               # 变化上报消息
        self.ioa_addrs = {50: [], 49: [], 45: []}       # IOA地址列表
        self.report_msg = {
            IEC104Config.TYPE_ID_REAL: [],
            IEC104Config.TYPE_ID_INT: [],
            IEC104Config.TYPE_ID_BOOL: []
        }  # 上报消息
        self.download_list: List = []       # 下发数据列表 [类型ID，地址，数值]
        self.last_broadcast_msg: Dict = {}  # 上次广播消息
        # 初始化数据存储
        self.data_real = data_real if data_real is not None else []
        self.data_int = data_int if data_int is not None else []
        self.data_bool = data_bool if data_bool is not None else []
        self.stop_flag = False          # 停止标志
        self.update_enabled = False     # 允许数据更新
        self.setup_server(host, port)   # 启动服务器
        public_lib.rich_info(IEC104Config.LOGGER_FILE, 1, "IEC104服务器状态", 
                       f"启动；监听地址：{host}，端口：{port}")

    @staticmethod
    def compare_dict(new_dict: Dict, last_dict: Dict) -> Dict:
        """比较新字典与旧字典，返回差异项"""
        def _compare_list(list1: List, list2: List) -> List:
            """比较两个列表的差异"""
            diff = []
            if len(list1) == len(list2):
                for i in range(len(list1)):
                    if list1[i][IEC104Config.VALUE_INDEX] != list2[i][IEC104Config.VALUE_INDEX]:
                        diff.append(list1[i])
            return diff

        result = {}
        for key in new_dict:
            tmp_list = []
            for num in range(len(new_dict[key])):
                tmp_list.append(_compare_list(new_dict[key][num], last_dict[key][num]))
            result[key] = tmp_list
        return result

    @staticmethod
    def increment_counter(value: int, step: int = 2) -> int:
        """递增计数器（默认收发序号累加2），超过0xFFFF归零"""
        value += step
        return value if value <= 0xFFFF else 0

    @staticmethod
    def split_list(lst: List, chunk_size: int) -> List[List]:
        """按指定大小分割列表"""
        return [lst[i:i+chunk_size] for i in range(0, len(lst), chunk_size)]

    @staticmethod
    def value_to_bytes(value: Any, type_id: int) -> bytes:
        """将值转换为字节串"""
        if type_id == IEC104Config.TYPE_ID_REAL:
            return struct.pack('<f', value)
        elif type_id == IEC104Config.TYPE_ID_INT:
            return struct.pack('<h', value)
        elif type_id == 'uint':
            return struct.pack('<H', value)
        elif type_id == IEC104Config.TYPE_ID_BOOL:
            return struct.pack('<B', value)
        return b''

    @staticmethod
    def data_to_bytes(data: List, type_id: int) -> bytes:
        """将数据列表转换为字节串"""
        null_byte = struct.pack('<B', 0x0)
        byte_data = b''
        for item in data:
            if type_id in (IEC104Config.TYPE_ID_REAL, IEC104Config.TYPE_ID_INT):
                # 结构: 点位地址 + 空字节 + 值 + 空字节 (共8字节)
                tmp = (IEC104Server.value_to_bytes(item[IEC104Config.IOA_INDEX], 'uint') +
                       null_byte +
                       IEC104Server.value_to_bytes(item[IEC104Config.VALUE_INDEX], type_id) +
                       null_byte)
            elif type_id == IEC104Config.TYPE_ID_BOOL:
                tmp = (IEC104Server.value_to_bytes(item[IEC104Config.IOA_INDEX], 'uint') +
                       null_byte +
                       IEC104Server.value_to_bytes(item[IEC104Config.VALUE_INDEX], type_id))
            else:
                tmp = b''
            byte_data += tmp
        return byte_data

    def apdu_to_bytes(self, client_data: types.SimpleNamespace, 
                     data_list: List, cot: int, type_id: int) -> Tuple[bytes, int, int]:
        """将APDU转换为字节串"""
        if not data_list:
            return b'', client_data.i_frame_tx_counter, client_data.i_frame_rx_counter

        # 帧头
        head = struct.pack('<B', 0x68)
        # 处理发送序号 (首次连接或总召唤不递增)
        if not (client_data.first_connect and cot != IEC104Config.COT_GENERAL_CALL):
            client_data.i_frame_tx_counter = self.increment_counter(client_data.i_frame_tx_counter)

        # 总召唤处理
        if cot == IEC104Config.COT_GENERAL_CALL:
            client_data.first_connect = False

        # 打包各字段
        seq_tx = struct.pack('<H', client_data.i_frame_tx_counter)
        seq_rx = struct.pack('<H', client_data.i_frame_rx_counter)
        type_id_pack = struct.pack('<B', type_id)
        obj_count = struct.pack('<B', len(data_list))
        cot_pack = struct.pack('<H', cot)
        public_addr = struct.pack('<H', 0x01)  # 公共地址

        # 信息块组装
        info = self.data_to_bytes(data_list, type_id)
        info_objects = seq_tx + seq_rx + type_id_pack + obj_count + cot_pack + public_addr + info
        length = struct.pack('<B', len(info_objects))

        return head + length + info_objects, client_data.i_frame_tx_counter, client_data.i_frame_rx_counter

    def handle_download_data(self, sock: socket.socket, client_data: types.SimpleNamespace,
                            rx_data: bytes, addrs: Dict) -> Tuple[int, int, List]:
        """处理下发数据"""
        tx = struct.unpack('<H', rx_data[2:4])[0]  # 主站发送序号
        rx = struct.unpack('<H', rx_data[4:6])[0]  # 主站接收序号
        public_addr = struct.unpack('<H', rx_data[10:12])[0]  # 公共地址
        type_id = rx_data[6]  # 类型ID
        rx_ioa = struct.unpack('<H', rx_data[12:14])[0]  # 接收IOA地址

        # 更新计数器
        client_data.i_frame_tx_counter = rx
        client_data.i_frame_rx_counter = self.increment_counter(tx)
        seq_tx = struct.pack('<H', client_data.i_frame_tx_counter)
        seq_rx = struct.pack('<H', client_data.i_frame_rx_counter)

        # 检查是否为支持的类型ID
        if type_id in (0x32, 0x2d, 0x31) :
            for ioa in addrs[type_id]:
                if rx_ioa == ioa and public_addr == IEC104Config.COA_ADDR:
                    # 发送确认帧
                    cot_confirm = struct.pack('<B', IEC104Config.COT_CONFIRM)
                    apdu_confirm = rx_data[:2] + seq_tx + seq_rx + rx_data[6:8] + cot_confirm + rx_data[9:]
                    sock.send(apdu_confirm)

                    # 发送停止帧
                    client_data.i_frame_tx_counter = self.increment_counter(client_data.i_frame_tx_counter)
                    seq_tx_stop = struct.pack('<H', client_data.i_frame_tx_counter)
                    cot_stop = struct.pack('<B', IEC104Config.COT_STOP)
                    apdu_stop = rx_data[:2] + seq_tx_stop + seq_rx + rx_data[6:8] + cot_stop + rx_data[9:]
                    sock.send(apdu_stop)

                    # 解析值
                    value = self._parse_download_value(rx_data, type_id)
                    return client_data.i_frame_tx_counter, client_data.i_frame_rx_counter, [type_id, rx_ioa, value]
            else:
                # 地址不匹配
                return self._send_unknown_frame(sock, rx_data, seq_tx, seq_rx, type_id,client_data)
        else:
            # 不支持的类型ID
            return self._send_unknown_frame(sock, rx_data, seq_tx, seq_rx, type_id,client_data)

    def _parse_download_value(self, rx_data: bytes, type_id: int) -> Any:
        """解析下发数据的值"""
        if type_id == 0x32:
            return round(struct.unpack('<f', rx_data[15:19])[0], 3)  # 浮点
        elif type_id == 0x2d:
            return rx_data[15]  # 布尔值
        elif type_id == 0x31:
            value = struct.unpack('<h', rx_data[15:17])[0]  # 有符号整数
            return max(-32768, min(32767, value))  # 边界检查
        return None

    def _send_unknown_frame(self, sock: socket.socket, rx_data: bytes, 
                           seq_tx: bytes, seq_rx: bytes, type_id: int,client_data: types.SimpleNamespace) -> Tuple[int, int, List]:
        """发送未知类型帧"""
        cot_unknown = struct.pack('<B', IEC104Config.COT_UNKNOWN)
        apdu = rx_data[:2] + seq_tx + seq_rx + rx_data[6:8] + cot_unknown + rx_data[9:]
        sock.send(apdu)
        return self.increment_counter(client_data.i_frame_tx_counter), client_data.i_frame_rx_counter, [type_id, None, None]

    def setup_server(self, host: str, port: int) -> None:
        """初始化服务器套接字"""
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((host, port))
        self.server_sock.listen(IEC104Config.MAX_CONNECTIONS)
        self.server_sock.setblocking(False)
        self.selector.register(self.server_sock, selectors.EVENT_READ, data=None)

    def start_broadcast(self) -> None:
        """启动定时广播"""
        self.broadcast_message(mode=0)
        # 每5秒广播一次 (可根据需要启用)
        # self.broadcast_timer = Timer(5.0, self.start_broadcast)
        # self.broadcast_timer.daemon = True
        # self.broadcast_timer.start()

    def broadcast_message(self, mode: int = 0) -> None:
        """向所有客户端广播消息"""
        if not self.clients or not self.update_enabled:
            return

        # 模式0: 变化上报; 模式1: 总召唤数据
        if mode == 0:
            if not self.last_broadcast_msg:
                self.last_broadcast_msg = copy.deepcopy(self.change_report_msg)
                update_flag = False
            else:
                self.report_msg = self.compare_dict(self.change_report_msg, self.last_broadcast_msg)
                self.last_broadcast_msg = copy.deepcopy(self.change_report_msg)
                update_flag = True
        else:
            self.report_msg = self.change_report_msg
            update_flag = True

        # 通知客户端更新
        if update_flag:
            for client_data in self.clients.values():
                client_data.update_enabled = True

    def accept_connection(self, sock: socket.socket) -> None:
        """接受新客户端连接"""
        conn, addr = sock.accept()
        public_lib.rich_info(IEC104Config.LOGGER_FILE, 1, "IEC104客户端连接", 
                       f"地址: {addr[0]}, 端口: {addr[1]}")
        conn.setblocking(False)
        
        # 客户端数据初始化
        client_data = types.SimpleNamespace(
            addr=addr,
            in_buffer=b"",
            out_buffer=b"",
            last_active=time.time(),
            i_frame_tx_counter=0,  # I帧发送计数器
            i_frame_rx_counter=0,  # I帧接收计数器
            s_frame_tx_counter=0,  # S帧发送计数器
            s_frame_rx_counter=0,  # S帧接收计数器
            update_enabled=False,  # 允许数据上送
            first_connect=True,    # 首次连接标志
            general_call_enabled=0,# 总召唤使能
            broadcast_list=[]      # 广播列表
        )
        
        self.selector.register(conn, selectors.EVENT_READ | selectors.EVENT_WRITE, data=client_data)
        self.clients[conn] = client_data

    def remove_client(self, sock: socket.socket) -> None:
        """移除断开连接的客户端"""
        if sock in self.clients:
            addr = self.clients[sock].addr
            public_lib.rich_info(IEC104Config.LOGGER_FILE, 1, "IEC104客户端断开", 
                           f"地址: {addr[0]}, 端口: {addr[1]}")
            self.selector.unregister(sock)
            del self.clients[sock]
            sock.close()
            

    def handle_read_event(self, sock: socket.socket, client_data: types.SimpleNamespace) -> None:
        """处理读事件"""
        try:
            recv_data = sock.recv(1024)
            if not recv_data:
                self.remove_client(sock)
                return

            client_data.in_buffer = recv_data
            client_data.last_active = time.time()  # 更新活动时间

            # 处理确认帧
            if recv_data.hex() == '680407000000':
                sock.send(bytes.fromhex('68040b000000'))
                return

            # 处理测试链路帧
            if recv_data.hex() == '680443000000':
                sock.send(bytes.fromhex('680483000000'))
                return

            # 处理总召唤确认帧
            if len(recv_data) > 6 and recv_data[0] == 0x68 and recv_data[6] == 0x64 and recv_data[8] == 0x06:
                self._handle_general_call_frame(sock, client_data, recv_data)
                return

            # 处理APDU命令帧
            if (len(recv_data) > 6 and recv_data[0] == 0x68 and 
                recv_data[6] in IEC104Config.COMMAND_SET and len(recv_data) > 12):
                tx, rx, down_data = self.handle_download_data(sock, client_data, recv_data, self.ioa_addrs)
                client_data.first_connect = False
                if down_data[2] is not None:
                    self.download_list.append(down_data)

        except (ConnectionError, OSError) as e:
            public_lib.rich_info(IEC104Config.LOGGER_FILE, 0, "读取数据错误", e)
            self.remove_client(sock)

    def _handle_general_call_frame(self, sock: socket.socket, client_data: types.SimpleNamespace, 
                                  recv_data: bytes) -> None:
        """处理总召唤帧"""
        tx = struct.unpack('<H', recv_data[2:4])[0]
        rx = struct.unpack('<H', recv_data[4:6])[0]
        public_addr = struct.unpack('<H', recv_data[10:12])[0]

        # 发送确认
        client_data.i_frame_tx_counter = rx
        client_data.i_frame_rx_counter = self.increment_counter(tx)
        seq_tx = struct.pack('<H', client_data.i_frame_tx_counter)
        seq_rx = struct.pack('<H', client_data.i_frame_rx_counter)
        cot_confirm = struct.pack('<B', IEC104Config.COT_CONFIRM)
        apdu_confirm = recv_data[:2] + seq_tx + seq_rx + recv_data[6:8] + cot_confirm + recv_data[9:]
        sock.send(apdu_confirm)

        # 总召唤数据上送
        if (public_addr == IEC104Config.COA_ADDR or public_addr == 0xffff) and self.general_call_msg:
            client_data.general_call_enabled = 1
            try:
                for type_id in [IEC104Config.TYPE_ID_REAL, IEC104Config.TYPE_ID_INT, IEC104Config.TYPE_ID_BOOL]:
                    for data in self.general_call_msg[type_id]:
                        apdu, tx_cnt, rx_cnt = self.apdu_to_bytes(
                            client_data, data, IEC104Config.COT_GENERAL_CALL, type_id
                        )
                        sock.send(apdu)
                        client_data.i_frame_tx_counter = tx_cnt
                        client_data.i_frame_rx_counter = rx_cnt
            except Exception as e:
                public_lib.rich_info(IEC104Config.LOGGER_FILE, 0, "总召唤数据上送错误", e)

        # 发送停止帧
        if (public_addr != IEC104Config.COA_ADDR and public_addr != 0xffff) or client_data.general_call_enabled:
            client_data.general_call_enabled = 0
            client_data.i_frame_tx_counter = self.increment_counter(client_data.i_frame_tx_counter)
            seq_tx_stop = struct.pack('<H', client_data.i_frame_tx_counter)
            cot_stop = struct.pack('<B', IEC104Config.COT_STOP)
            apdu_stop = recv_data[:2] + seq_tx_stop + seq_rx + recv_data[6:8] + cot_stop + recv_data[9:]
            sock.send(apdu_stop)

    def handle_write_event(self, sock: socket.socket, client_data: types.SimpleNamespace) -> None:
        """处理写事件"""
        if not client_data.update_enabled:
            return

        try:
            # 发送各类数据帧
            for type_id in [IEC104Config.TYPE_ID_REAL, IEC104Config.TYPE_ID_INT, IEC104Config.TYPE_ID_BOOL]:
                for data in self.report_msg[type_id]:
                    if data:
                        apdu, tx_cnt, rx_cnt = self.apdu_to_bytes(
                            client_data, data, IEC104Config.COT_CHANGE, type_id
                        )
                        sock.send(apdu)
                        client_data.i_frame_tx_counter = tx_cnt
                        client_data.i_frame_rx_counter = rx_cnt
                        client_data.first_connect = False
            client_data.update_enabled = False
        except (ConnectionError, OSError) as e:
            public_lib.rich_info(IEC104Config.LOGGER_FILE, 0, f"发送数据到 {client_data.addr} 错误", e)
            self.remove_client(sock)

    def update_ioa_addrs(self) -> None:
        """更新IOA地址列表"""
        code_data_map = {
            50: self.data_real,
            49: self.data_int,
            45: self.data_bool
        }
        for code, data_list in code_data_map.items():
            try:
                self.ioa_addrs[code] = [item[IEC104Config.IOA_INDEX] for item in data_list]
            except Exception as e:
                public_lib.rich_info(IEC104Config.LOGGER_FILE, 0, "更新IOA地址错误", e)
                self.ioa_addrs[code] = []

    def service_connection(self, key: selectors.SelectorKey, mask: int) -> None:
        """处理客户端连接的读写事件"""
        sock = key.fileobj
        client_data = key.data

        if mask & selectors.EVENT_READ:
            self.handle_read_event(sock, client_data)
        if mask & selectors.EVENT_WRITE:
            self.handle_write_event(sock, client_data)

    def run(self) -> None:
        """运行服务器主循环"""
        try:
            while not self.stop_flag:
                events = self.selector.select(timeout=0.5)
                for key, mask in events:
                    if key.data is None:
                        self.accept_connection(key.fileobj)
                    else:
                        self.service_connection(key, mask)

                # 更新消息帧
                self.change_report_msg = {
                    IEC104Config.TYPE_ID_REAL: self.split_list(self.data_real, IEC104Config.MAX_FRAME_REAL),
                    IEC104Config.TYPE_ID_BOOL: self.split_list(self.data_bool, IEC104Config.MAX_FRAME_COIL),
                    IEC104Config.TYPE_ID_INT: self.split_list(self.data_int, IEC104Config.MAX_FRAME_INT)
                }
                self.general_call_msg = self.change_report_msg

                # 检查是否有数据需要更新
                self.update_enabled = not (not self.data_real and not self.data_int and not self.data_bool)
                if self.update_enabled:
                    self.update_ioa_addrs()

                # 广播变化数据
                self.broadcast_message()

                # 处理下发数据 (根据实际需求启用)
                # self.process_downloaded_data()

                time.sleep(0.05)

        except KeyboardInterrupt:
            public_lib.rich_info(IEC104Config.LOGGER_FILE, 1, "IEC104服务器中断", "异常终止")
        finally:
            self._cleanup_resources()

    def process_downloaded_data(self) -> None:
        """处理下发数据（需根据实际业务逻辑实现）"""
        if not self.download_list:
            return
        for item in self.download_list:
            self.data_real = self._update_data_list(self.data_real, item)
            self.data_bool = self._update_data_list(self.data_bool, item)
            self.data_int = self._update_data_list(self.data_int, item)
        self.download_list = []

    def _update_data_list(self, data_list: List, update_item: List) -> List:
        """更新数据列表中的值"""
        type_id, addr, value = update_item
        for idx, item in enumerate(data_list):
            if item[3] == type_id and item[IEC104Config.IOA_INDEX] == addr:
                data_list[idx][IEC104Config.VALUE_INDEX] = value
                break
        return data_list

    def _cleanup_resources(self) -> None:
        """清理服务器资源"""
        if self.broadcast_timer:
            self.broadcast_timer.cancel()
        # 关闭所有客户端连接
        for sock in list(self.clients.keys()):
            self.remove_client(sock)
        # 关闭服务器套接字
        self.selector.unregister(self.server_sock)
        self.server_sock.close()
        self.selector.close()
        public_lib.rich_info(IEC104Config.LOGGER_FILE, 1, "IEC104服务器状态", "已关闭")



def simulate_data(server: IEC104Server) -> None:
    """模拟数据更新（示例）"""
    import random
    ac0 = 0
    while not server.stop_flag:
        # 每5秒更新一次模拟数据
        if server.clients and ac0 >= 5:
            print(server.download_list)
            
            server.data_real = [
                [i, round(random.random() * 100, 2), IEC104Config.TYPE_ID_REAL, 50, '模拟浮点'] 
                for i in range(1, 102)
            ]
            ac0 = 0
        ac0 += 1
        time.sleep(1)


def server_run(host: str = IEC104Config.HOST, port: int = IEC104Config.PORT) -> None:
    """启动服务器"""
    server = IEC104Server(host, port)
    # 启动模拟数据线程
    sim_thread = Thread(target=simulate_data, args=(server,), daemon=True)
    sim_thread.start()
    server.run()


if __name__ == "__main__":
    server_run()