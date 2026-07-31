from flask import Flask, request, jsonify
import time
import threading
import json
import logging
from wsgiref.simple_server import make_server, WSGIServer,WSGIRequestHandler
from socketserver import ThreadingMixIn  # 提供多线程能力
import psutil
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

# 禁用WSGI服务器的原生打印输出（关键！不是logging，是服务器自带的print）
class SilentWSGIRequestHandler(WSGIRequestHandler):
    """重写请求处理器，彻底关闭原生日志打印"""
    def log_request(self, code='-', size='-'):
        # 重写log_request方法，什么都不做，禁用请求日志
        pass
    def log_error(self, format, *args):
        # 可选：如果想保留错误日志，注释这行；想彻底关就保留
        pass
    def log_message(self, format, *args):
        # 禁用所有服务器消息打印
        pass


# 自定义多线程WSGI服务器类
class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    """支持多线程的WSGI服务器"""
    # 关闭线程守护（可选，让线程正常退出）
    daemon_threads = True

# 配置
class WebAPI:
    def __init__(self, ip='127.0.0.1', port=5000):
        # 配置参数
        self.ip = ip
        self.port = port
        self.receive_path = '/post'
        self.send_path = '/get'
        self.log_path = 'log_web_api.txt'
        
        # 数据存储与状态标志
        self.received_data = {}     # snap7Client获取这里的值写入到PLC
        self.send_data = {}         # snap7Client赋值到这里
        self.received_flag = False
        
        # 线程与服务器相关
        self.app = Flask(__name__)
        self.http_server = None
        self.server_thread = None
        # self.lock = threading.Lock()  # 线程安全锁
        
        # 初始化配置
        self._setup_logging()
        self._setup_routes()
        self.app.config['JSON_AS_ASCII'] = False

    def _setup_logging(self):
        """配置日志级别"""
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.CRITICAL)
        # logging.getLogger('wsgiref').setLevel(logging.ERROR)

    def _setup_routes(self):
        """注册路由"""
        self.app.add_url_rule('/', methods=["GET", "POST"], view_func=self.index)
        self.app.add_url_rule(
            self.receive_path, 
            methods=['POST'], 
            view_func=self.store_data
        )
        self.app.add_url_rule(
            self.send_path, 
            methods=['GET'], 
            view_func=self.get_data
        )

    def index(self):
        """根路径响应"""
        return 'welcome to web api'

    def store_data(self):
        """处理POST请求，存储接收的数据"""
        try:
            data = json.loads(request.data)
            if not isinstance(data, dict):
                return jsonify({"error": "输入必须为字典类型"}), 400
                
            # with self.lock:  # 确保线程安全
            self.received_data.update(data)
            self.received_flag = True
                
            return jsonify({"rec_message": 1}), 200
            
        except json.JSONDecodeError as e:
            return jsonify({"error": f"JSON解析错误->{str(e)}"}), 400
        except Exception as e:
            return jsonify({"error": f"处理失败->{str(e)}"}), 500

    def get_data(self):
        """处理GET请求，返回存储的数据"""
        # with self.lock:  # 确保线程安全
        return jsonify(self.send_data), 200

    def _run_server(self):
        """服务器运行逻辑"""
        try:
            self.http_server = make_server(self.ip,
                                        self.port, 
                                        self.app.wsgi_app, 
                                        server_class=ThreadedWSGIServer,
                                        handler_class=SilentWSGIRequestHandler  # 关键：指定静默请求处理器
                                        )
            self.http_server.serve_forever()
        except Exception as e:
            public_lib.rich_info(self.log_path,1,'WebApi服务器状态',f'启动失败->{str(e)}')
            self.http_server = None

    def start(self):
        listen_port = check_port_listen(self.port)
        if listen_port:
            public_lib.rich_info(self.log_path,1,'WebApi服务器状态',f'端口{self.port}已被其他进程占用，无法启动')
            return
        else:
            """启动服务器线程"""
            if not self.server_thread or not self.server_thread.is_alive():
                self.server_thread = threading.Thread(target=self._run_server, daemon=True)
                self.server_thread.start()
                public_lib.rich_info(self.log_path,1,'WebApi服务器状态',f'启动；监听地址：{self.ip}，端口：{self.port}')

    def stop(self):
        """停止服务器"""
        if self.http_server:
            self.http_server.shutdown()
            # self.server_thread.join(timeout=1)  # 等待线程结束
            self.http_server = None
            public_lib.rich_info(self.log_path,1,'WebApi服务器状态',f'已停止')

def simulate_run(ip='127.0.0.1', port=5000):
    import random
    web_api = WebAPI(ip, port)
    web_api.start()
    num = 0
    try:
        while True:
            data_dict = {1:'jjdj',2:'ki1'}
            for i in data_dict:
                data_dict[i] = random.sample('jkdj123478',5)
            web_api.send_data = data_dict
            time.sleep(1)
            num += 1
            # 模拟服务器重启逻辑
            if num == 1130:
                print("服务器重启")
                web_api.stop()
            elif num > 1132:
                num = 0
                web_api.start()
                print("服务器重启成功")
    except KeyboardInterrupt:
        web_api.stop()
        print("服务器已手动停止")

if __name__ == '__main__':
    simulate_run()