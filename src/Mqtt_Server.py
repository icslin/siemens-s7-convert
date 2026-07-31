import paho.mqtt.client as mqtt
import time
import json
import os
import public_lib
import threading
client = None
    
class MQTTFileClient:
    """MQTT客户端类，实现MQTT连接、消息收发及文件传输功能"""
    
    def __init__(self):
        # 配置参数
        self.config = self._load_config()
        self.client_id = self.config["ID"]
        self.broker_url = self.config["地址"]
        self.broker_port = self.config["端口"]
        self.ssl_enabled = self.config["是否使用加密"]
        self.sub_topic = self.config["订阅主题"]
        self.pub_topic = self.config["发布主题"]
        self.username = self.config["账户"]
        self.password = self.config["密码"]
        self.remote_pwd = self.config["远程密码"]
        
        # 主题配置
        self.topic_download = f"{self.sub_topic}/download"
        self.topic_upload = f"{self.sub_topic}/upload"
        self.topic_feedback = f"{self.sub_topic}/feedback"
        
        # 状态变量
        self.client = None
        self.connected = False
        self.program_restart = False
        self.mqtt_exit = False
        self.pub_error = False
        self.write_enabled = False
        self.send_done = False
        self.write_done = False
        self.error_enabled = False
        self.global_message = ""
        self.logger_file = "log_MQTT.txt"
        self.transfer_log = "file_transfer.txt"

        # 初始化客户端
        self._init_client()

    def _load_config(self):
        """加载配置文件，返回配置字典"""
        default_config = {
            "ID": "paho_mqtt_python",
            "地址": "127.0.0.1",
            "端口": 1883,
            "是否使用加密": "0",
            "订阅主题": "sensor3",
            "账户": "",
            "密码": "",
            "远程密码": "",
            "发布主题": ""
        }
        
        try:
            cfg = public_lib.cfg_read('config.ini', 'UTF-8')
            # 合并配置，覆盖默认值
            for key in default_config:
                if key in cfg:
                    default_config[key] = cfg[key]
            # 转换端口为整数
            default_config["端口"] = int(default_config["端口"])
            return default_config
        except Exception as e:
            self._log_info(self.logger_file, 0, "配置加载", f"配置文件读取失败，使用默认配置: {e}")
            return default_config

    def _init_client(self):
        """初始化MQTT客户端"""
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, self.client_id)
            self.client.on_connect = self._on_connect
            self.client.on_message = self._on_message
            self.client.username_pw_set(self.username, self.password)
            
            # 配置SSL
            if self.ssl_enabled == '1':
                self.client.tls_set(ca_certs='emqxsl-ca.crt')
        except Exception as e:
            self._log_info(self.logger_file, 0, "客户端初始化", f"初始化失败: {e}")
            raise

    def _log_info(self, log_file, level, module, message):
        """封装日志记录方法"""
        public_lib.rich_info(log_file, level, module, message)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """连接成功回调函数"""
        self._log_info(self.logger_file, 1, "MQTT连接", "成功连接到MQTT服务端！")
        self.connected = True
        # 订阅主题
        client.subscribe(self.sub_topic)
        client.subscribe(self.topic_download)
        client.subscribe(self.topic_feedback)
        client.subscribe(self.topic_upload)

    def _publish_message(self, topic, payload):
        """封装消息发布方法"""
        try:
            self.client.publish(topic=topic, payload=payload)
            self.pub_error = False
        except Exception as e:
            self._log_info(self.logger_file, 0, "MQTT发布", f"发布失败: {e}")
            self.pub_error = True

    def _upload_file(self, filename):
        """上传文件到服务器"""
        try:
            file_hash, data = public_lib.cal_hash(filename)
            file_size = os.stat(filename).st_size
            base_filename = os.path.basename(filename)
            
            # 发送文件元信息
            payload = {
                "filename": base_filename,
                "file_hash": file_hash,
                "wd_en": 1,
                "send_done": 0,
                "file_hide": 0,
                "file_size": file_size
            }
            self._publish_message(self.topic_upload, json.dumps(payload))
            
            # 发送文件内容
            with open(filename, 'rb') as f:
                while True:
                    chunk = f.read(1024 * 10)  # 10KB chunks
                    if not chunk:
                        # 发送完成标志
                        payload.update({"send_done": 1, "wd_en": 0})
                        self._publish_message(self.topic_upload, json.dumps(payload))
                        break
                    self._publish_message(self.topic_upload, chunk)
                    time.sleep(0.1)  # 控制发送速率
        except Exception as e:
            self._log_info(self.transfer_log, 0, "文件上传", f"上传失败 {filename}: {e}")

    def _write_file_chunk(self, filename, data):
        """写入文件片段"""
        with open(filename, 'ab') as f:
            f.write(data)

    def _on_message(self, client, userdata, msg):
        """消息接收回调函数"""
        topic = msg.topic
        payload = msg.payload
        
        if topic == self.sub_topic:
            self.global_message = payload
            
        elif topic == self.topic_feedback:
            self._handle_feedback(payload)
            
        elif topic == self.topic_download:
            self._handle_download(payload)

    def _handle_feedback(self, payload):
        """处理反馈主题消息"""
        try:
            payload_str = payload.decode()
            # 处理控制命令
            if payload_str == f'restart@{self.remote_pwd}':
                self.program_restart = True
            elif payload_str == f'move@{self.remote_pwd}':
                move_result = public_lib.move_file('TMP')
                self._publish_message(self.topic_feedback, json.dumps(move_result))
            elif payload_str == f'clean@{self.remote_pwd}':
                clean_result = public_lib.clean_file('TMP')
                self._publish_message(self.topic_feedback, json.dumps(clean_result))
            elif payload_str == f'get@{self.remote_pwd}':
                file_list = {"file_lists": public_lib.get_filePath()}
                self._publish_message(self.topic_feedback, json.dumps(file_list))

            # 处理JSON格式反馈
            try:
                data = json.loads(payload_str)
                # 处理文件上传结果
                if "file_name" in data and "upload_done" in data:
                    status = "成功" if data["upload_done"] == 1 else "失败"
                    self._log_info(self.transfer_log, 1, "文件传输", f"文件 {data['file_name']} 上传{status}")
                
                # 处理文件删除
                if "del_file" in data:
                    file_lists = public_lib.get_filePath()
                    for file_key in data["del_file"]:
                        if file_key in file_lists:
                            public_lib.remove_file(file_lists[file_key])
                
                # 处理文件上传请求
                if "upload_file" in data:
                    file_lists = public_lib.get_filePath()
                    for file_key in data["upload_file"]:
                        if file_key in file_lists:
                            self._upload_file(file_lists[file_key])
            except json.JSONDecodeError:
                pass  # 非JSON格式消息忽略
        except Exception as e:
            self._log_info(self.logger_file, 0, "反馈处理", f"处理失败: {e}")

    def _handle_download(self, payload):
        """处理文件下载逻辑"""
        try:
            # 尝试解析JSON元数据
            try:
                meta_data = json.loads(payload)
                filename = meta_data["filename"]
                rec_hash = meta_data["file_hash"]
                self.write_done = meta_data["wd_en"]
                self.send_done = meta_data["send_done"]
                is_hide = meta_data["file_hide"]
                
                # 初始化文件接收
                if self.write_done == 1:
                    try:
                        os.remove('tmp_data')  # 清除旧临时文件
                    except OSError:
                        pass
                    self.write_enabled = True

            except json.JSONDecodeError:
                # 非JSON格式视为文件内容
                if self.write_enabled and not self.send_done:
                    self._write_file_chunk('tmp_data', payload)
                return

            # 处理文件接收完成
            if self.send_done == 1 and self.write_enabled:
                self.write_enabled = False
                file_hash, file_data = public_lib.cal_hash('tmp_data')
                
                # 校验文件哈希
                if file_hash == rec_hash:
                    self._log_info(self.transfer_log, 1, "文件传输", f"文件 {filename} 接收成功")
                    # 保存文件到TMP目录
                    save_path = f'./TMP/{filename}'
                    with open(save_path, "wb") as f:
                        f.write(file_data)
                    # 设置文件属性
                    if is_hide == 1:
                        os.system(f'attrib +s +h {save_path}')
                    else:
                        os.system(f'attrib -s -h {save_path}')
                    feedback = {"file_name": filename, "wd_done": 1}
                else:
                    feedback = {"file_name": filename, "wd_done": 0}
                
                self._publish_message(self.topic_feedback, json.dumps(feedback))
                os.remove('tmp_data')  # 清理临时文件

        except Exception as e:
            self._log_info(self.transfer_log, 0, "文件下载", f"处理失败: {e}")

    def connect(self):
        """连接到MQTT服务器"""
        try:
            self.mqtt_exit = False
            self.program_restart = False
            self.client.connect(self.broker_url, self.broker_port, 60)
            self.client.loop_start()
            self.error_enabled = False
            # public_lib.info(self.logger_file,1,'MQTT连接',f"成功连接到MQTT服务端！")
            return 0
        except Exception as e:
            if not self.error_enabled:
                self.error_enabled = True
                self._log_info(self.logger_file, 0, "MQTT连接", 
                              f"连接出错（检查网络和参数）: {e}")
            return 404

    def run(self):
        """主运行循环"""
        first_scan = False
        state_err = False
        while True:
            if not first_scan:
                state = self.connect()
                first_scan = True
            # 处理退出信号
            if self.mqtt_exit:
                self.connected = False
                first_scan = False
                self.client.disconnect()
                self._log_info(self.logger_file, 1, "MQTT客户端状态", "手动停止！")
                break
            
            # 处理重连
            if state == 404:

                time.sleep(5)
                state = self.connect()
                if not state_err :
                    state_err = True
                    self._log_info(self.logger_file, 0, "MQTT客户端状态","MQTT连接出错，重新连接中...")
            else:
                state_err = False
            
            time.sleep(1)

def mqtt_main():
    """程序入口"""
    public_lib.run_path(__file__)
    global client
    try:
        client = MQTTFileClient()
        t1 = threading.Thread(target=client.run,daemon=True)
        t1.start()
        return client
    except Exception as e:
        print(f"程序运行出错: {e}")
        return None
if __name__ == '__main__':
    mqtt_client = mqtt_main()
    while True:
        time.sleep(1)
        if mqtt_client != None and mqtt_client.program_restart :
            print(mqtt_client.program_restart)
            mqtt_client.mqtt_exit = True
            time.sleep(8)
            mqtt_client = mqtt_main()
            print(mqtt_client)


