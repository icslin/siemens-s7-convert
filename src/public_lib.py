import time
import hashlib
import os
import shutil
import sys
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.console import Console
from rich.layout import Layout
from threading import Lock
system_type=os.name # 获取系统类型
nt_en = 1

log_lists = []
max_logs_len = 10       # 缓存最大日志条数
console = Console()
live_display: Live = None
live_lock = Lock()      # 线程安全锁
log_lock = Lock()             # 保护 log_lists 的锁
live_started = False    # 标记Live是否已启动
top_message = ""              # 顶部固定信息（支持多行）

#上升沿：结果变量，全局变量=trig(输入信号，全局变量)
def trigger_P(in0,temp):
    result = 0
    if in0 == 1 and temp == 0:
        temp = 1
        result = 1
    if in0 == 0 :
        temp = 0
    return result,temp

# 获取当前格式化时间
def NowTime(mode=0):
    if mode == 0 :
        # 时间格式 年-月-日 时:分:秒
        now_time = time.strftime('%Y-%m-%d %H:%M:%S',time.localtime())         #格式化时间
    elif mode == 1 :
        # 时间格式 年月日_时分
        now_time = time.strftime('%Y%m%d_%H%M',time.localtime())
    elif mode == 2 :
        # 时间格式 年/月/日/
        now_time = time.strftime('%Y/%m/%d',time.localtime())
    elif mode == 3 :
        # 时间格式 年月日
        now_time = time.strftime('%Y%m%d',time.localtime())
    else:
        now_time = None
    return now_time

# 打印信息并记录，需要colorama库支持
def info(nfile,mode,name,info):
    global nt_en
    # 记录日志
    def logger(nfile,text):
        with open(nfile,'a') as f:
            f.write(f'{text}\n')
    nt = NowTime()
    if mode == 0 :
        text = f'[{nt}] [E] {name}: {info}'
        if nt_en == 1:
            # 输出错误信息前导
            pt = f'[{nt}] \033[0;31m[E]\033[0m \033[0;0m{name}: {info}\033[0m '
        else:
            pt = text
    elif mode == 1 :
        text= f'[{nt}] [I] {name}: {info}'
        if nt_en == 1:
            # 输出一般信息前导
            pt = f'[{nt}] \033[0;32m[I]\033[0m \033[0;0m{name}: {info}\033[0m'
        else:
            pt = text
    elif mode == 2 :
        text = f'[{nt}] {name}: {info}'
    else:
        pt = f'[{nt}] {name}: {info}'
        text = f'[{nt}] {name}: {info}'
    logger(nfile,text)
    if mode != 2 :
        print(pt+'\n',end='')

# ---------- 设置顶部信息 ----------
def set_top_message(msg: str):
    """外部调用此函数更新顶部固定信息（支持多行），并自动启动/刷新 Live"""
    global top_message
    top_message = msg
    _init_live()
    if live_started and live_display:
        live_display.update(generate_layout())  # 只更新数据，自动刷新会处理绘制

# ---------- 生成完整布局 ----------
def generate_layout():
    """每次刷新都重新生成布局，顶部高度根据内容动态调整"""
    with log_lock:
        logs_copy = log_lists.copy()

    # 构造日志表格（底部）
    table = Table(
        show_header=True,
        header_style="bold blue",
        expand=True,
        show_lines=True,
        padding=(0, 1)
    )
    table.add_column("序号", style="dim", width=5)
    table.add_column("日志内容（顶部最新）", ratio=1)
    for idx, log_text in enumerate(reversed(logs_copy)):
        table.add_row(str(idx + 1), log_text)

    bottom_panel = Panel(
        table,
        title="[bold cyan]缓存日志区[/bold cyan]",
        border_style="cyan"
    )

    # 计算顶部内容所需行数（支持多行）
    top_lines = top_message.splitlines()
    content_lines = max(1, len(top_lines))   # 至少 1 行
    # 面板边框占用 2 行，再加一行内边距（视觉舒适）
    top_height = content_lines + 3

    top_panel = Panel(
        top_message if top_message else "（等待信息）",
        title="[bold green]程序信息[/bold green]",
        border_style="green"
    )

    # 构建布局
    layout = Layout()
    layout.split(
        Layout(name="top", size=top_height),
        Layout(name="bottom"),
    )
    layout["top"].update(top_panel)
    layout["bottom"].update(bottom_panel)
    return layout

# ---------- 初始化 Live（启用自动刷新，使用 screen 彻底重绘） ----------
def _init_live():
    global live_display, live_started
    with live_lock:
        if live_started:
            return
        live_display = Live(
            generate_layout(),
            auto_refresh=True,            # 启用自动刷新，窗口变化会自动重绘
            refresh_per_second=2,         # 每秒刷新 2 次，平衡性能和响应
            screen=True,                  # 使用 alternate screen，消除残留
            console=console,
            vertical_overflow="scroll",
            transient=False
        )
        live_display.start()
        live_started = True
        # 无需手动 refresh，自动刷新会在启动后很快显示

# ---------- 日志主函数 ----------
def rich_info(nfile, mode, name, info):
    """
    mode <= 0 : 红色 [E] 错误，同时显示在屏幕并写入文件
    mode == 1 : 绿色 [I] 信息，同时显示在屏幕并写入文件
    mode == 2 : 仅写入文件，不在屏幕显示
    mode >=3  : 仅打印，不写入文件
    """
    def logger(nfile, text):
        with open(nfile, 'a') as f:
            f.write(f'{text}\n')

    nt = NowTime()
    if mode <= 0:
        text = f'[{nt}] [red][E][/red] {name}: {info}'
        log_text = f'[{nt}] [E] {name}: {info}'
    elif mode == 1:
        text = f'[{nt}] [green][I][/green] {name}: {info}'
        log_text = f'[{nt}] [I] {name}: {info}'
    elif mode == 2:
        log_text = f'[{nt}] {name}: {info}'
    else:
        text = f'[{nt}] [cyan][D][/cyan] {name}: {info}'
    if mode <3 :
        try:
            logger(nfile, log_text)
        except Exception as err:
            pass
    if mode != 2 :
        with log_lock:
            log_lists.append(text)
            while len(log_lists) > max_logs_len:
                log_lists.pop(0)
        _init_live()
        live_display.update(generate_layout())  # 只更新，自动刷新会绘制

# ---------- 关闭 Live ----------
def close_live():
    global live_display, live_started
    with live_lock:
        if live_started and live_display:
            live_display.stop()
            live_started = False

# 计算二进制文件哈希值
def cal_hash(filename):
    with open(filename, 'rb') as file:
        datas = file.read()
    file_hash = hashlib.md5(datas).hexdigest()
    return file_hash,datas
# 写入二进制文件
def wb_data(filename,data):
    with open(filename, 'ab') as file:
        file.write(data) 
# 读配置文件
def cfg_read(text,code='utf-8'): 
    '''
    读取文件，文件每行格式为：
    名称=内容
    跨行中括号内容读取
    返回一个字典
    ---
    text: 文件名
    code: 文件编码
    '''
    dic={}
    # 去字符串空格
    def clear_space(s):
        return s.replace(' ','')
    with open(text,'r',encoding=code) as f :
        data = f.read()
        data = data.split('\n')
        tmp = []
        specil = 0
        for row in data:
            try:
                if row[0] !='#' and len(row) != 0:
                    if ('[' not in row or ('[' in row and ']' in row) ) and specil == 0:
                        if '#' in row:
                            row = row.split('#')[0]
                        row = row.split('=')
                        key = clear_space(row[0])
                        value = clear_space(row[1])
                        dic[key] = value
                    else:
                        specil = 1
                        pass
                if len(row) != 0 and specil == 1:
                    if '#' in row:
                            row = row.split('#')[0]
                    tmp.append(row)
                    if ']' in row:
                        specil = 0
                        tmp = ''.join(tmp)
                        tmp = tmp.replace(' ','')
                        tmp = (tmp.split('='))
                        key = clear_space(tmp[0])
                        value = clear_space(tmp[1])
                        dic[key] = value
                        tmp = []
            except Exception as e:
                pass
    return dic 
# 移动指定的下一个文件夹的所有文件文件并覆盖当前的文件夹
def move_file(folder):
    try:
        path1 = os.getcwd()                     # 当前路径
        listdir0 = os.listdir(f'./{folder}')    # 待移动指定文件夹里的文件列表
        list_file_path = [f'{path1}\\{folder}\\{i}' for i in listdir0]    # 待移动的文件路径
        list_file_path2 = [f'{path1}\\{i}' for i in listdir0]             # 移动文件的当前文件夹路径
        for i,j in enumerate(list_file_path)  :
            try:
                shutil.move(j,path1)
            except Exception as err:
                print(err)
                os.remove(list_file_path2[i])
                shutil.move(j,path1)
        return {"move_file":1}
    except Exception as err:
        info('./snap7/log.txt',1,'move_file',err)
        return {"move_file":404}
# 清空指定文件夹里的文件
def clean_file(folder):
    try:
        # path1=os.getcwd()                     # 当前路径
        # listdir0=os.listdir(f'./{folder}')    # 待移动指定文件夹里的文件列表
        # list_file_path=[f'{path1}\\{folder}\\{i}' for i in listdir0]    # 待移动的文件路径
        path = get_filePath(f'./{folder}',1)
        for i in path:
            # os.remove(path[i])
            os.remove(i)
        return {"clean_file":1}
    except Exception as err:
        info('./snap7/log.txt',1,'clean_file',err)
        return {"clean_file":404}
# 新建文件夹
def mk_folder(folder_name,hide=0,SysTpye='nt'):
        # 获取当前文件所在路径
        path = os.path.abspath('')
        # 获取当前文件夹下的所有文件列表
        ffname_list = os.listdir(path)
        # 当前程序目录无"folder_name"文件时创建
        if folder_name in ffname_list :
            pass
        else:
            os.mkdir(folder_name)
        if system_type == 'nt' and SysTpye == 'nt':
            if hide == 1 :
                os.system(f'attrib +s +h {folder_name}')
            else:
                os.system(f'attrib -s -h {folder_name}')
# 删除指定文件
def remove_file(file_path):
    try:
        os.remove(file_path)
        return True
    except Exception as err:
        return False
        print(err)
# 获取文件路径,0字典，1列表
def get_filePath(folderPath=None,mode=0):
    # 指定路径
    if folderPath != None:
        current_directory=folderPath
    else:
        # 获取当前目录下的所有文件路径
        current_directory = os.getcwd()
    # 使用os.walk()遍历当前目录下的所有文件和子目录
    num = 0
    if mode == 0:
        file_lists={}
    else:
        file_lists=[]
    for root, dirs, files in os.walk(current_directory):
        for file in files:
            if mode == 0:
                file_lists[num]=os.path.join(root, file)
            else:
                file_lists.append(os.path.join(root, file))
            num += 1
    return (file_lists)
# 判断文件夹是否存在
def exists_folder(path):
    if not os.path.exists(path):
        os.makedirs(path)
# 指定字符串前插入
def strInsert(inStr,index,value):
        return  ''.join([value+i if i == index else i for n,i in enumerate(inStr)])
# 指定位置前插入字符串
def insert_str(inStr,index,value):
    '''
    inStr: 原始字符串
    index: 插入位置
    value: 要插入的字符串
    '''
    tmp = list(inStr)
    tmp.insert(index, value)
    return ''.join(tmp)
# 是否隐藏文件
def hide_file(fname,mode):
    '''
    fname: 文件名
    mode: 1隐藏，非1取消隐藏
    '''
    try:
        if mode == '1' :
            os.system(f'attrib +h +s {fname}')
        else :
            os.system(f'attrib -h -s {fname}')
    except:
        pass
# 判断文件是否存在，不存在则创建
def exists_file(file_path,data=''):
    exists_en = True
    try:
        with open(file_path,'r') as f:
            exists_en = True
    except Exception as err:
        with open(file_path,'a') as f:
            if data != '':
                f.write(data)
            exists_en = False
    return exists_en
# 程序或源码运行路径  ，exe_path='',              
def run_path(script_path=__file__):
    try:
        # 如果程序被打包为可执行文件
        if getattr(sys, 'frozen', False):
            # 获取可执行文件所在的目录
            BASE_PATH = os.path.dirname(sys.executable)
            # print(f'程序执行路径：{BASE_PATH}')
            # 将当前工作目录设置为可执行文件所在的目录
            os.chdir(BASE_PATH)
        else:
            # 如果程序作为脚本运行，使用脚本目录
            BASE_PATH = os.path.dirname(script_path)
            # print(f'脚本执行路径：{BASE_PATH}')
            os.chdir(BASE_PATH)
    except Exception as e:
        print(f'获取执行路径失败：{e}')
if system_type == 'nt':
    try:
        # 打印可输出带颜色的文字
        from colorama import init
        init(autoreset=True)
    except:
        nt_en = 0
    # 程序开机自启动设置方法
    def autorun_snap7(mode,log_path='log.txt'):
        def autorun(Ctrl_En,path):
            # Ctrl_En=使能，path=启动路径和程序
            import winreg
            Key = winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER,r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',access=winreg.KEY_WRITE)
            if path != None :
                try:
                    if Ctrl_En == 1 :
                        # 新建键值
                        winreg.SetValueEx(Key,'autorun_snap7',0,winreg.REG_SZ,path)
                    else :
                        #删除键值
                        winreg.DeleteValue(Key, "autorun_snap7")
                except Exception as err:
                    pass
            Key.Close()
        path1 = os.getcwd()       # 当前路径
        listdir0 = os.listdir()   # 当前文件列表
        name_exe = ''             # exe程序名称
        for i in listdir0 :
            if i[-3:] == 'exe' :
                name_exe = i
                break
        if mode == 1 :
            if name_exe == '' :
                info(log_path,1,'autorun_snap7','找不到exe程序')
                path = None
            else :
                path = f"{path1}'\'{name_exe}"
            autorun(mode,path)
# 16进制字符串转10进制整型
def str_hex2dec(s):
    odata = 0
    su = s.upper()
    for c in su:
        tmp = ord(c)
        if tmp <= ord('9') :
            odata = odata << 4
            odata += tmp - ord('0')
        elif ord('A') <= tmp <= ord('F'):
            odata = odata << 4
            odata += tmp - ord('A') + 10
    return odata

# 高低字节调转（data为int类型）
def swap(data):
    h=0xff00
    l=0xff
    data=((data&l)<<8)|((data&h)>>8)
    return data
# crc16校验算法，返回16进制字符串（data为内容为16进制的字符串）
def crc16(data):
    # 16进制字符串转10进制整型列表（每两个字符生成一个元素添加到列表）
    def str_hex2dec_list(data):
        data = data.replace(' ','')     # 去空字符
        temp = []                       # 整数数字列表
        a = 0
        b = 0
        for i in range(int(len(data)/2)):
            a = data[b]+data[b+1]
            a = str_hex2dec(a)
            temp.append(a)
            b += 2
        return temp
    datas = str_hex2dec_list(data)
    crc = int(0xFFFF)
    for i in datas:
        crc = i ^ crc
        for j in range(8):
            if crc & int(0b01):
                crc = (crc>>1) ^ int(0xa001)
            else :
                crc = crc>>1
    crc = swap(crc)
    return f'{data}{crc:04x}' 
if __name__ == '__main__':
    '''
    16进制字符串转16进制数，先转整数再转16进制
    value = int('1fff',16)
    print(hex(value))
    
    '''
    code = '010300000001'
    print(crc16(code))
    value = int(crc16(code),16)
    print(hex(value))

