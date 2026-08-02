import selectors
import socket
import struct
import time
from threading import Thread
import types
import public_lib

# 常量定义
DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 502
DEFAULT_ADDR = 1
LOGGER_FILE = 'log_Modbus_Server.txt'

# Modbus功能码
READ_HOLDING_REGISTERS = 0x03    # 读保持寄存器
WRITE_SINGLE_REGISTER = 0x06     # 写单个寄存器
WRITE_MULTIPLE_REGISTERS = 0x10  # 写多个寄存器

# Modbus错误码 (字节码)
ILLEGAL_FUNCTION = {
    0x01: '03018101',
    0x02: '03018201',
    0x04: '03018401',
    0x05: '03018501',
    0x0F: '03018501',
}
ILLEGAL_DATA_ADDRESS_03 = '03018302'       # 03功能码非法地址响应
ILLEGAL_DATA_ADDRESS_06 = '03018602'       # 06地址超限
ILLEGAL_DATA_ADDRESS_10 = '03019002'       # 10地址超限


class ModbusServer:
    """
    Modbus TCP服务器实现，支持以下功能码：
    - 0x03: 读取保持寄存器
    - 0x06: 写入单个寄存器
    - 0x10: 写入多个寄存器
    """

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, blocks=None):
        """
        初始化Modbus服务器
        :param host: 监听主机地址
        :param port: 监听端口
        :param blocks: 寄存器数据块，格式 {设备ID: [寄存器列表]}
        """
        self.selector = selectors.DefaultSelector()
        self.clients = {}       # 客户端连接字典 {socket: 连接数据}
        self.stop_flag = False  # 服务器停止标志
        self.blocks = blocks if blocks is not None else {}  # 寄存器数据块
        self.wd_list = []       # 等待写入的数据记录
        self.server_sock = None # 服务器套接字
        
        self._setup_server(host, port)
        self._start_server_thread()

    def _setup_server(self, host, port):
        """初始化服务器套接字并开始监听"""
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((host, port))
        self.server_sock.listen()
        public_lib.rich_info(LOGGER_FILE, 1, 'Modbus服务器状态', 
                       f'启动；监听地址：{host}，端口：{port}')
        
        self.server_sock.setblocking(False)
        self.selector.register(self.server_sock, selectors.EVENT_READ, data=None)

    def _start_server_thread(self):
        """启动服务器运行线程"""
        self.server_thread = Thread(target=self.run, daemon=True)
        self.server_thread.start()

    @staticmethod
    def _chunk_list(lst, chunk_size):
        """按指定大小分割列表"""
        return [lst[i:i+chunk_size] for i in range(0, len(lst), chunk_size)]

    @staticmethod
    def _data_to_bytes(data):
        """将整数列表转换为Modbus格式的字节串（大端序）"""
        byte_data = b''
        for value in data:
            byte_data += struct.pack('>H', value)
        return byte_data

    def _accept_connection(self, sock):
        """接受新的客户端连接"""
        conn, addr = sock.accept()
        public_lib.rich_info(LOGGER_FILE, 1, 'Modbus客户端连接', 
                       f'地址：{addr[0]}，端口：{addr[1]}')
        
        conn.setblocking(False)
        client_data = types.SimpleNamespace(
            addr=addr,
            inb=b"",
            outb=b"",
            last_active=time.time()
        )
        events = selectors.EVENT_READ | selectors.EVENT_WRITE
        self.selector.register(conn, events, data=client_data)
        self.clients[conn] = client_data

    def _remove_client(self, sock):
        """移除断开的客户端连接"""
        if sock in self.clients:
            addr = self.clients[sock].addr
            public_lib.rich_info(LOGGER_FILE, 1, 'Modbus客户端断开', 
                           f'地址：{addr[0]}，端口：{addr[1]}')
            self.selector.unregister(sock)
            del self.clients[sock]
            sock.close()

    def _handle_read_holding_registers(self, unit_id, start_addr, length, recv_data):
        """处理读保持寄存器请求 (0x03)"""
        if unit_id not in self.blocks:
            return b''

        # 检查地址范围
        register_data = self.blocks[unit_id]
        end_addr = start_addr + length
        if end_addr > len(register_data):
            return recv_data[:5]+bytes.fromhex(ILLEGAL_DATA_ADDRESS_03)

        # 提取数据并构建响应
        response_data = register_data[start_addr:end_addr]
        byte_length = struct.pack('<B', len(response_data) * 2)
        response_payload = recv_data[6:8] + byte_length + self._data_to_bytes(response_data)
        length_field = struct.pack('<B', len(response_payload))
        
        return recv_data[:5] + length_field + response_payload

    def _handle_write_single_register(self, unit_id, addr, value,recv_data):
        """处理写单个寄存器请求 (0x06)"""
        if unit_id not in self.blocks:
            return b''

        # 检查地址有效性
        if addr >= len(self.blocks[unit_id]):
            return recv_data[:5]+bytes.fromhex(ILLEGAL_DATA_ADDRESS_06)

        # 更新寄存器值
        self.blocks[unit_id][addr] = value
        self.wd_list.append([unit_id, addr, value])
        return None  # 成功时返回原请求作为响应

    def _handle_write_multiple_registers(self, unit_id, start_addr, data_bytes, recv_data):
        """处理写多个寄存器请求 (0x10)"""
        # 检查ID有效性
        if unit_id not in self.blocks:
            return b''

        # 解析写入数据
        byte_chunks = self._chunk_list(list(data_bytes), 2)
        values = [chunk[0] << 8 | chunk[1] for chunk in byte_chunks]
        end_addr = start_addr + len(values)

        # 检查寄存器地址范围
        if end_addr > len(self.blocks[unit_id]):
            return recv_data[:5]+bytes.fromhex(ILLEGAL_DATA_ADDRESS_10)

        # 更新寄存器值
        for i, value in enumerate(values):
            self.blocks[unit_id][start_addr + i] = value
        self.wd_list.append([unit_id, start_addr, values])

        # 构建响应报文
        response_payload = recv_data[6:12]
        length_field = struct.pack('<B', 6)  # 固定响应长度
        return recv_data[:5] + length_field + response_payload

    def _service_connection(self, key, mask):
        """处理客户端连接的数据收发"""
        sock = key.fileobj
        client_data = key.data

        if mask & selectors.EVENT_READ:
            try:
                recv_data = sock.recv(1024)
                if not recv_data:
                    self._remove_client(sock)
                    return

                client_data.last_active = time.time()
                send_data = self._process_request(recv_data)
                if send_data:
                    sock.send(send_data)

            except (ConnectionError, OSError) as e:
                public_lib.rich_info(LOGGER_FILE, 0, '客户端连接错误', str(e))
                self._remove_client(sock)

    def _process_request(self, recv_data):
        """解析并处理Modbus请求报文"""
        try:
            # 解析MBAP头部和功能码
            mbap_protocol_id = struct.unpack('>H', recv_data[2:4])[0]
            mbap_length = struct.unpack('>H', recv_data[4:6])[0]
            data_length = len(recv_data[6:])

            # 基本合法性检查
            if len(recv_data) < 12 or mbap_protocol_id != 0x00 or mbap_length != data_length:
                return b''

            unit_id = recv_data[6]
            function_code = recv_data[7]
            start_addr = struct.unpack('>H', recv_data[8:10])[0]
            data_value = struct.unpack('>H', recv_data[10:12])[0]

            # 处理不同功能码
            if function_code == READ_HOLDING_REGISTERS:
                return self._handle_read_holding_registers(
                    unit_id, start_addr, data_value, recv_data)

            elif function_code == WRITE_SINGLE_REGISTER:
                error = self._handle_write_single_register(unit_id, start_addr, data_value,recv_data)
                return error if error else recv_data

            elif function_code == WRITE_MULTIPLE_REGISTERS:
                write_data = recv_data[13:]  # 提取写入数据部分
                return self._handle_write_multiple_registers(
                    unit_id, start_addr, write_data, recv_data)

            # 处理未支持的功能码
            elif function_code in ILLEGAL_FUNCTION:
                return recv_data[:5] + bytes.fromhex(ILLEGAL_FUNCTION[function_code])

            return b''

        except Exception as e:
            public_lib.rich_info(LOGGER_FILE, 0, '请求处理错误', str(e))
            return b''

    def run(self):
        """服务器主循环"""
        try:
            while not self.stop_flag:
                events = self.selector.select(timeout=0.5)
                for key, mask in events:
                    if key.data is None:
                        self._accept_connection(key.fileobj)
                    else:
                        self._service_connection(key, mask)
                time.sleep(0.05)  # 降低CPU占用

        except KeyboardInterrupt:
            public_lib.rich_info(LOGGER_FILE, 1, 'Modbus服务器', '异常中断')
        finally:
            self._cleanup_resources()

    def _cleanup_resources(self):
        """清理服务器资源"""
        # 关闭所有客户端连接
        for sock in list(self.clients.keys()):
            self._remove_client(sock)
        
        # 关闭服务器套接字
        if self.server_sock:
            self.selector.unregister(self.server_sock)
            self.server_sock.close()
        
        self.selector.close()
        public_lib.rich_info(LOGGER_FILE, 1, 'Modbus服务器状态', '已停止')

    def stop(self):
        """停止服务器"""
        self.stop_flag = True

if __name__ == "__main__":
    try:
        source_data = {
            1: [16712, 0, 2, 3, 4, 5, 6, 7, 1, 12],
            2: [11,12,13]
            }
        server = ModbusServer(host=DEFAULT_HOST, port=DEFAULT_PORT, blocks=source_data)
        while True:
            # print(server.blocks)
            # print(server.wd_list)
            time.sleep(1)   
    except KeyboardInterrupt:
        server.stop()
