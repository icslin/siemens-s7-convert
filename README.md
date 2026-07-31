# 西门子 S7 通讯协议转换工具说明文档

<p align="center">
<a href="https://img.shields.io/badge/Python-3.8%2B-blue"><img alt="Version" title="Version" src="https://img.shields.io/badge/Python-3.8%2B-blue" /></a>
<a href="https://img.shields.io/badge/License-MIT-green"><img alt="License" title="License" src="https://img.shields.io/badge/License-MIT-green" /></a>
<a href="https://img.shields.io/badge/Star-欢迎点亮-orange"><img alt="Lint & Test" title="Lint & Test" src="https://img.shields.io/badge/Star-欢迎点亮-orange" /></a>
<br/>

## 一、项目概述

### 1.1 核心定位

本工具是一款基于 Python 开发的工业级通讯协议转换中间件，核心功能为通过 Snap7 协议读取西门子 PLC 的 DB 块与 M 区数据，并提供 Modbus TCP、MQTT、Web API、IEC104 四种标准化协议的读写转发能力，实现西门子 PLC 与第三方系统（SCADA、MES、数据平台等）的无缝数据交互。具备数据归档查阅功能，适用一些售前设备数据采集分析。

### 1.2 技术栈

* 核心依赖库：python-snap7 3.0.0（PLC 数据读写）、paho-mqtt 2.1.0（MQTT 通讯）、Flask 3.1.1（Web API 服务）、rich 15.0.0（日志可视化）
* 开发语言：Python（兼容 Python 3.8 及以上版本）、html、JavaScript

* 开源协议：MIT License

### 1.3 适用场景与稳定性

* 兼容 PLC 型号：西门子 S7-1200 系列、S7-1500 系列、S7-200 SMART 系列、S7-400（均经过实测验证）

* 运行环境：Windows（32/64 位）、Linux（CentOS/Ubuntu 等）

* 稳定性：已在实际工业项目中稳定运行 1 年以上，无重大通讯故障记录

## 二、核心功能特性

### 2.1 多源数据读写支持

* 支持多台 PLC（S7-1200/1500）同时接入，实现 DB 块与 M 区地址的并行读写转发

* 支持数据类型：布尔型（Bool）、字节型（Byte）、整型（Int）、浮点型（Real），覆盖工业常见数据格式

### 2.2 多协议转发能力

| 协议类型       | 功能角色      | 默认配置参数                   |
| ---------- | --------- | ------------------------ |
| Modbus TCP | 服务端       | 地址：127.0.0.6，端口：502，ID：1 |
| MQTT       | 客户端 / 发布端 | 支持 JSON 格式上报，模板可自定义      |
| Web API    | 服务端       | 地址：127.0.0.1，端口：5000     |
| IEC104     | 服务端       | 监听地址：0.0.0.0，端口：2404     |

### 2.3 通讯性能优化

* 批量读取机制：一次性读取目标 DB 块全部字节后进行解析，减少通讯交互次数，降低网络负载

* 读取时长控制：单次批量读取耗时控制在 1 秒（视网络带宽与 PLC 响应速度调整）

* 可配置读取频率：通过 config.ini 设置 Snap7 读取周期，默认 1 秒 / 次，毫秒级可设置0.3到0.5秒

### 2.4 便捷运维特性

* 开机自启动：支持 Windows 平台开机自动运行，无需人工干预

* 灵活配置：点位信息通过 CSV 文件配置，协议参数通过 ini 文件调整，无需修改源码

* 日志可视化：关键操作（连接状态、数据读写、协议交互）均输出着色日志，便于问题排查

## 三、安装部署指南

### 3.1 快速部署（无需安装）

1. 下载工具压缩包，解压至任意目录

2. 必备文件清单（解压后需完整保留）：

   * 可执行文件：Snap7Client3.5.8.exe

   * 依赖文件：config.ini、ztest.csv（点位配置文件）

3. 直接双击运行 Snap7Client3.5.\*.exe（windwos平台）或执行 snap7client3.5.*（linux平台），即可启动服务

### 3.2 源码部署（Windows/Linux 通用）

#### 3.2.1 环境准备

1. 安装 Python 3.8 及以上版本（需配置系统环境变量）

2. 安装依赖库：

    ``` bash
    # Windows平台
    pip install -r requirements.txt

    # Linux平台
    pip3 install -r requirements.txt
    ```

#### 3.2.2 必备文件清单

需确保源码目录下包含以下文件：

* 核心脚本：Snap7Client3.5.*.py、Mqtt_Server.py、public_lib.py、iec104_Server.py、Modbus_Server.py、web_api.py、Sqlite_OP.py、data_viewer.py

* 配置文件：config.ini（协议参数配置）、ztest.csv（点位配置文件，可自定义命名）

* 依赖库：python-snap7、paho-mqtt、colorama、Flask、rich（通过 pip 安装后自动关联）

#### 3.2.3 启动命令

```
# Windows平台
python Snap7Client3.5.*.py

# Linux平台
python3 Snap7Client3.5.*.py
```

## 四、详细配置说明

### 4.1 核心配置文件（config.ini）

用于配置 PLC 连接参数、协议端口、读取频率等核心参数，示例配置如下：

``` ini
#------Snap7------
#控制器地址，端口默认102
#每个plc一个参数列表，多个plc参数用逗号隔开,ID唯一不重复
#plc参数列表说明(plc地址,modbus ID（唯一）,plc名称(唯一),点位文件路径csv，plc机架号，plc卡槽号【200smart、1200和1500使用机架号和卡槽号使用0，0即可】)
控制器参数 = [
            ('127.0.0.1',1,'plc1','ztest.csv',0,0),  # 1号配置
            #('192.168.2.10',2,'plc2','ztest.csv',0,0),  # 2号配置
            ]
#读控制器频率，单位秒;1个plc读2000个字节内，读5个plc，1秒内
读取频率 = 1
#------Snap7------
```

### 4.2 点位配置文件（ztest.csv）

用于定义 PLC 数据点位的映射关系，字段说明与示例如下：

| 名称   | 数据类型 | 起始位置 | DB 块地址 | 权限 | IEC104 地址 |数据库表名 |说明               |
| :-----: | :----: | :--: | :----: | :--: | :---------: |:---------:| ---------------- |
| 布尔 0 | Bool | 0.0  | 1      | 读写 | 1         | 布尔| Bool 类型格式：字节偏移。位 |
| 布尔 1 | Bool | 0.1  | 1      | 读写 | 2         | 布尔|                 |
| 浮点 0 | Real | 2    | 1      | 读写 | 10        | 浮点|Real 类型占 4 字节    |
| 整数 0 | Int  | 6    | 1      | 读写 | 20        | 整型|Int 类型占 2 字节     |
| 字节 0 | Byte | 0    | 1      | 读写 | 30        | 字节|Byte 类型占 1 字节    |

#### 配置规则：

1. 多个csv点位文件，IEC104地址也必须唯一，不可重复

2. 起始位置说明：
    - Bool 类型：格式为「字节偏移。位」（如 0.1 表示 DB1 的 0 字节第 1 位）

    - 非 Bool 类型（Byte/Int/DInt/Word/DWord/Real）：直接填写字节偏移量（可以直接在博图DB块的编辑器中直接拷贝到点位配置文件）

3. 仅支持表中指定的数据类型
   - [x] 布尔（Bool）
   - [x] 字节（Byte）
   - [x] 整型（Int/DInt/Word/DWord）
   - [x] 浮点型（Real）
4. 数据库表名，有填写则归档到填写的表名里，无则不记录，数据库文件在snap7/sqlite目录下
### 4.3 Modbus 点表生成

工具会自动根据点位配置文件生成 Modbus TCP 映射表（plc1_modbus 点位表.csv），映射关系示例如下：

| 名称   | Modbus 地址 | 数据类型 | 权限 | 上位机数据标识   | 上位机数据类型 |
| ---- | --------- | ---- | -- | --------- | ------- |
| 布尔 0 | 0         | Bool | 读写 | plc1@布尔 0 | Int |
| 整数 0 | 8         | Int  | 读写 | plc1@整数 0 | Int     |
| 浮点 0 | 9         | Real | 读写 | plc1@浮点 0 | Float   |
| 字节 0 | 11        | Byte | 读写 | plc1@字节 0 | Int     |

## 五、使用指南

[🔗实操传送门](https://blog.icsteam.cn/2025/07/18/201_snap7_readme/)

### 5.1 数据读取操作

#### 5.1.1 Web API 读取

* 请求方式：GET

* 请求地址：http://127.0.0.1:5000/get

* 响应示例：

```json
{
 "properties": {
  "plc1@字节0": 12,
   "plc1@布尔0": 0,
   "plc1@布尔2": 1,
   "plc1@整数0": -125,
   "plc1@浮点0": 0.98,
   "plc1@状态": 1
 }
}
```

#### 5.1.2 IEC104 读取

1. 启动工具后，IEC104 服务监听 0.0.0.0:2404

2. 客户端连接地址：`127.0.0.1:2404`，站地址 1

3. 通过客户端工具（如 IEC104 Client Simulator）查询对应 IEC104 地址的点位数据

#### 5.1.3 Modbus TCP 读取

1. 使用 Modbus 工具（如 Modbus Poll）连接服务端：

     * IP：127.0.0.1，端口：666，从站 ID：2

2. 按照生成的 Modbus 点表地址进行数据读取

### 5.2 数据写入操作

#### 5.2.1 Web API 写入

* 请求方式：POST

* 请求地址：http://127.0.0.1:5000/post

* 请求体示例：

``` json
{
 "SetProperty": {
   "plc1@字节0": 12,
   "plc1@浮点0": 0.98
 }
}
```

#### 5.2.2 IEC104 写入

* 客户端连接后，向目标 IEC104 地址发送写入请求

* 示例操作：写入 PLC1 DB1 起始地址 2（浮点型）数据 0.98

#### 5.2.3 MQTT 写入

1. 订阅主题：`/plc/data/control`

2. 发布 JSON 格式控制指令：

``` json
{
 "SetProperty": {
   "plc1@布尔3": 1,
   "plc1@整数0": 256
 }
}
```

## 六、技术细节与性能指标

### 6.1 数据读取机制

* 字节计算规则：要读取的总字节数 = 最高偏移量 - 最小偏移量 + 最高偏移量对应数据类型的字节长度

* 示例：若点位包含起始位置 0（Byte）、2（Real）、6（Int），则总字节数 = 6 - 0 + 2（Int 类型字节数）=8 字节

* 读取延迟：网络稳定时≤1秒，支持通过 config.ini 调整读取频率适配弱网环境

### 6.2 协议兼容性

* Snap7 协议：兼容西门子 S7 系列 PLC 的 S7 通讯规范

* IEC104 协议：支持总召唤、布尔、整形、浮点数的读写

* Modbus TCP：符合 Modbus RTU over TCP 规范，支持功能码 03（读取保持寄存器）、06（写入单个寄存器）、16（写入多个寄存器）

* MQTT 协议：支持 MQTT 3.1.1 版本，支持 QoS 0/1 等级

### 6.3 资源占用

* 内存占用：运行时≤50MB（Windows 平台）、≤30MB（Linux 平台）

* CPU 占用：单 PLC 连接时≤5%，多 PLC 并行时≤10%（基于 Intel i5 处理器测试）

## 七、注意事项与故障排查

### 7.1 配置注意事项

1. IEC104 地址必须全局唯一，不可重复配置
2. 点位配置文件中，数据类型仅支持 Bool、Byte、Int、DInt、Word、DWord、Real、
3. Linux 平台下需确保配置文件（config.ini、ztest.csv）的权限为 root 可读写
4. Linux 平台下注意modbus协议的端口配置



### 7.2 常见故障排查

| 故障现象        | 可能原因                  | 解决方案                                      |
| ----------- | --------------------- | ----------------------------------------- |
| 无法连接 PLC    | PLC IP 配置错误 / 网络不通    | 检查 config.ini 中 PLC\_IP\_LIST，测试 ping 连通性 |
| 协议客户端无法连接工具 | 端口被占用                 | 更换 config.ini 中对应协议端口，重启工具                |
| 数据读取为空      | 点位配置错误（DB 块 / 起始位置）   | 核对 PLC 实际点位与 ztest.csv 配置是否一致             |
| MQTT 连接失败   | MQTT Broker 地址 / 端口错误 | 检查 config.ini 中 MQTT 配置，测试 Broker 连通性     |
|反复连接断开PLC|点位配置DB块地址不存在或配置点位不存在|检查点位配置文件CSV文件是否正确|

## 八、版本更新记录

| 版本号    | 更新日期       | 核心更新内容       |
| ------ | ---------- | ------------------------- |
| v3.5.11 | 2026-07-31 | 修复图表小数点显示问题，修复超80字段后字段筛选数据显示异常的bug，优化modbus和iec104写逻辑 |
| v3.5.10 | 2026-07-28 | 新增sqlite数据库查询，导出功能;修复远程重启，一致累加设备实例的bug;优化打印区域 |
| v3.5.9 | 2026-07-25 | 优化modbus写逻辑 |
| v3.5.8 | 2026-07-12 | 增加测点归档功能，使用Sqlite数据库 |
| v3.5.6 | 2026-07-10 | 使用豆包AI轻量重构;修复iec104协议写有符号数据显示问题;修复mqtt和webapi下发不存在的标签或plc名时无限循环的问题 |
| v3.5.5 | 2026-03-21 | 转换协议下发写plc数据，支持超时时重发，限定重发次数,snap7库更新为3.0.0 |
| v3.5.4 | 2025-12-29 | 使用豆包AI重构自定义模块MQTT/Modbus/IEC104/Web API的代码，优化结构，并重新调试 |
| v3.5.3 | 2025-12-09 | 修复开机自启动功能在linux系统下的报错，开机自启动只在windwos下有效 |
| v3.5.2 | 2025-11-08 | 增加对M区数据的读写DB编号地址为0时即是读写M区的数据 |
| v3.5.1 | 2025-11-05 | 把点位的数据类型字符统一转成小写            |
| v3.5.0 | 2025-07-02 | 弃用modbus-tk库，新增modbus tcp服务端，重写modbus交互方法     |
## 九、声明与联系方式

### 9.1 开源声明

本工具基于 MIT License 开源，可自由用于商业与非商业项目，使用时需保留原作者版权声明。工具中使用的第三方开源库（snap7、paho-mqtt 等）遵循其各自的开源协议。

### 9.2 联系方式

* 作者：LinJiefeng（工控万金油）

* 邮箱：i@icssteam.cn

* 项目地址：[https://gitee.com/icsteam/siemens-s7-convert](https://gitee.com/icsteam/siemens-s7-convert)

* 相关工具参考：

  * Snap7 官网：[https://snap7.sourceforge.net/](https://snap7.sourceforge.net/)

  * Eclipse Paho：[https://eclipse.dev/paho/](https://eclipse.dev/paho/)

  * S7 协议调试工具：[https://blog.csdn.net/weixin\_44112083/article/details/130627005](https://blog.csdn.net/weixin_44112083/article/details/130627005)

  * IEC104 调试工具：[https://www.redisant.cn/iec104client](https://www.redisant.cn/iec104client)
