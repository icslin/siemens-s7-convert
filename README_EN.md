# Siemens S7 Communication Protocol Conversion Tool Documentation

# Siemens S7 Communication Protocol Conversion Tool Documentation



## 1\. Project Overview

### 1\.1 Core Positioning

This tool is an industrial\-grade communication protocol conversion middleware developed in Python\. Its core function is to read DB block and M area data from Siemens PLCs via the Snap7 protocol, and provide read/write forwarding capabilities for four standardized protocols: Modbus TCP, MQTT, Web API and IEC104\. It enables seamless data interaction between Siemens PLCs and third\-party systems \(SCADA, MES, data platforms, etc\.\), and supports data archiving and querying for pre\-sales equipment data collection and analysis scenarios\.

### 1\.2 Technology Stack

- Core dependency libraries: python\-snap7 3\.0\.0 \(PLC data read/write\), paho\-mqtt 2\.1\.0 \(MQTT communication\), Flask 3\.1\.1 \(Web API service\), rich 15\.0\.0 \(visual logging\)

- Development languages: Python \(compatible with Python 3\.8 and above\), HTML, JavaScript

- Open\-source license: MIT License

### 1\.3 Applicable Scenarios \& Stability

- Compatible PLC models: Siemens S7\-1200 series, S7\-1500 series, S7\-200 SMART series, S7\-400 \(all verified by actual testing\)

- Operating environments: Windows \(32/64\-bit\), Linux \(CentOS, Ubuntu, etc\.\)

- Stability: Deployed stably in live industrial projects for more than 1 year with no major communication failure records

## 2\. Core Functional Features

### 2\.1 Multi\-source Data Read/Write Support

- Simultaneous access to multiple PLCs \(S7\-1200/1500\) for parallel read\-write and forwarding of DB block and M area addresses

- Supported data types: Bool, Byte, Int, Real, covering common industrial data formats

### 2\.2 Multi\-protocol Forwarding Capabilities

|Protocol Type|Role|Default Configuration Parameters|
|---|---|---|
|Modbus TCP|Server|Address: \[127\.0\.0\.6\]\(127\.0\.0\.6\), Port: 502, Slave ID: 1|
|MQTT|Client / Publisher|Supports JSON\-format reporting, customizable templates|
|Web API|Server|Address: \[127\.0\.0\.1\]\(127\.0\.0\.1\), Port: 5000|
|IEC104|Server|Listening Address: \[0\.0\.0\.0\]\(0\.0\.0\.0\), Port: 2404|

### 2\.3 Communication Performance Optimization

- Batch reading mechanism: Read all bytes of the target DB block at once then parse data, reducing communication round trips and network load

- Read duration control: Single batch read limited to 1 second \(adjustable based on network bandwidth and PLC response speed\)

- Configurable read interval: Set Snap7 read cycle via config\.ini; default 1 second per scan, millisecond intervals of 0\.3–0\.5 seconds supported

### 2\.4 Convenient O\&M Features

- Auto\-start on boot: Supports automatic startup on Windows without manual intervention

- Flexible configuration: Point information configured via CSV files; protocol parameters adjusted via INI files, no source code modification required

- Visual colored logs: Colored output for key operations \(connection status, data read/write, protocol interaction\) to facilitate troubleshooting

## 3\. Installation \& Deployment Guide

### 3\.1 Quick Deployment \(No Installation Required\)

1. Download the tool archive and extract it to any directory

2. Mandatory files to retain after extraction:

    - Executable: Snap7Client3\.5\.8\.exe

    - Config files: config\.ini, ztest\.csv \(point mapping configuration\)

3. Double\-click Snap7Client3\.5\.\*\.exe \(Windows\) or run `snap7client3.5.*` \(Linux\) to launch the service directly

### 3\.2 Source Code Deployment \(Cross\-platform for Windows/Linux\)

#### 3\.2\.1 Environment Preparation

1. Install Python 3\.8 or higher \(system environment variables must be configured\)

2. Install dependency libraries:

```bash
# Windows
pip install -r requirements.txt
# Linux
pip3 install -r requirements.txt
```

#### 3\.2\.2 Mandatory Source Directory Files

Ensure the following files exist in the source folder:

- Core scripts: Snap7Client3\.5\.\*\.py, Mqtt\\\[\_Server\.py\]\(\_Server\.py\), public\\\[\_lib\.py\]\(\_lib\.py\), iec104\\\[\_Server\.py\]\(\_Server\.py\), Modbus\\\[\_Server\.py\]\(\_Server\.py\), web\\\[\_api\.py\]\(\_api\.py\), Sqlite\\\[\_OP\.py\]\(\_OP\.py\), data\\\[\_viewer\.py\]\(\_viewer\.py\)

- Config files: config\.ini \(protocol parameters\), ztest\.csv \(point mapping file, customizable name\)

- Dependencies: python\-snap7, paho\-mqtt, colorama, Flask, rich \(automatically linked after pip installation\)

#### 3\.2\.3 Startup Commands

```bash
# Windows
python Snap7Client3.5.*.py
# Linux
python3 Snap7Client3.5.*.py
```

## 4\. Detailed Configuration Instructions

### 4\.1 Core Configuration File \(config\.ini\)

Used to configure PLC connection parameters, protocol ports, read frequency and other core settings\. Sample configuration below:

```ini
#------Snap7------
# Controller IP, default port 102
# One parameter tuple per PLC; separate multiple PLCs with commas, unique IDs mandatory
# PLC parameter tuple format: (PLC IP, Modbus Slave ID (unique), PLC Name (unique), CSV point file path, Rack No., Slot No.)
# For S7-200 SMART / S7-1200 / S7-1500, use Rack=0, Slot=0
ControllerList = [
            ('127.0.0.1',1,'plc1','ztest.csv',0,0),  # PLC 1 config
            #('192.168.2.10',2,'plc2','ztest.csv',0,0),  # PLC 2 config
            ]
# PLC scan interval, unit: second; stable for 5 PLCs within 1 second when reading ≤2000 bytes per PLC
ScanInterval = 1
#------Snap7------
```

### 4\.2 Point Mapping File \(ztest\.csv\)

Defines mapping relationships for PLC data points\. Field definitions and sample entries below:

|Name|Data Type|Start Offset|DB Block|Access|IEC104 Address|SQL Table|Description|
|---|---|---|---|---|---|---|---|
|Bool0|Bool|0\.0|1|R/W|1|bool\_data|Bool format: byte offset\.bit position|
|Bool1|Bool|0\.1|1|R/W|2|bool\_data||
|Real0|Real|2|1|R/W|10|real\_data|Real occupies 4 bytes|
|Int0|Int|6|1|R/W|20|int\_data|Int occupies 2 bytes|
|Byte0|Byte|0|1|R/W|30|byte\_data|Byte occupies 1 byte|

#### Configuration Rules

1. IEC104 addresses must be globally unique across all CSV point files, no duplicates allowed

2. Start Offset explanation:

    - Bool type: Format `byte.bit` \(e\.g\. 0\.1 = bit 1 of byte 0 in DB1\)

    - Non\-Bool types \(Byte/Int/DInt/Word/DWord/Real\): Enter raw byte offset \(can be copied directly from TIA Portal DB editor\)

3. Supported data types only:

    * [x] Bool

    * [x] Byte

    * [x] Int / DInt / Word / DWord

    * [x] Real

4. SQL Table field: Data archived to the specified table if filled; no archiving if blank\. SQLite database files stored under `snap7/sqlite/`

### 4\.3 Auto\-generated Modbus Point Table

The tool automatically generates a Modbus TCP mapping CSV \(`plc1_modbus point list.csv`\) based on the point configuration file\. Sample mapping:

|Name|Modbus Address|Data Type|Access|Client Tag ID|Client Data Type|
|---|---|---|---|---|---|
|Bool0|0|Bool|R/W|plc1@Bool0|Int|
|Int0|8|Int|R/W|plc1@Int0|Int|
|Real0|9|Real|R/W|plc1@Real0|Float|
|Byte0|11|Byte|R/W|plc1@Byte0|Int|

## 5\. Operation Guide

[🔗 Full Tutorial](https://blog.icsteam.cn/2025/07/18/201_snap7_readme/)

### 5\.1 Data Read Operations

#### 5\.1\.1 Web API Read

- Request Method: GET

- Request URL: [http://127\.0\.0\.1:5000/get](http://127.0.0.1:5000/get)

- Sample Response:

```json
{
 "properties": {
  "plc1@Byte0": 12,
  "plc1@Bool0": 0,
  "plc1@Bool2": 1,
  "plc1@Int0": -125,
  "plc1@Real0": 0.98,
  "plc1@Status": 1
 }
}
```

#### 5\.1\.2 IEC104 Read

1. After tool startup, IEC104 server listens on \[0\.0\.0\.0:2404\]\(0\.0\.0\.0:2404\)

2. Client connection parameters: `127.0.0.1:2404`, Station Address = 1

3. Query data for target IEC104 addresses via simulation tools \(e\.g\. IEC104 Client Simulator\)

#### 5\.1\.3 Modbus TCP Read

1. Connect via Modbus tools \(e\.g\. Modbus Poll\):

    - IP: \[127\.0\.0\.1\]\(127\.0\.0\.1\), Port: 666, Slave ID: 2

2. Read data using addresses defined in the generated Modbus point table

### 5\.2 Data Write Operations

#### 5\.2\.1 Web API Write

- Request Method: POST

- Request URL: [http://127\.0\.0\.1:5000/post](http://127.0.0.1:5000/post)

- Sample Request Body:

```json
{
 "SetProperty": {
   "plc1@Byte0": 12,
   "plc1@Real0": 0.98
 }
}
```

#### 5\.2\.2 IEC104 Write

1. Establish client connection, send write requests to target IEC104 address

2. Example: Write value 0\.98 to Real point at byte offset 2 in DB1 of PLC1

#### 5\.2\.3 MQTT Write

1. Subscribe topic: `/plc/data/control`

2. Publish control instructions in JSON format:

```json
{
 "SetProperty": {
   "plc1@Bool3": 1,
   "plc1@Int0": 256
 }
}
```

## 6\. Technical Details \& Performance Metrics

### 6\.1 Data Reading Mechanism

- Byte calculation rule: Total read bytes = Max Offset \- Min Offset \+ Byte length of data type at Max Offset

- Example: Points with offsets 0 \(Byte\), 2 \(Real\), 6 \(Int\): Total bytes = 6 \- 0 \+ 2 \(Int byte length\) = 8 bytes

- Read latency: ≤1 second under stable network; adjust scan interval in config\.ini for weak network environments

### 6\.2 Protocol Compatibility

- Snap7: Compliant with Siemens S7 communication specifications for S7\-series PLCs

- IEC104: Supports general interrogation, read/write for boolean, integer and floating\-point values

- Modbus TCP: Compliant with Modbus RTU over TCP standards; supports Function Code 03 \(Read Holding Registers\), 06 \(Write Single Register\), 16 \(Write Multiple Registers\)

- MQTT: Supports MQTT 3\.1\.1, QoS 0 / QoS 1

### 6\.3 Resource Consumption

- Memory footprint: ≤50MB \(Windows\), ≤30MB \(Linux\)

- CPU usage: ≤5% with single PLC connection; ≤10% for multiple parallel PLC connections \(tested on Intel i5 CPU\)

## 7\. Precautions \& Troubleshooting

### 7\.1 Configuration Notes

1. All IEC104 addresses must be globally unique with no duplicates

2. Supported point data types limited to: Bool, Byte, Int, DInt, Word, DWord, Real

3. Linux environment: Ensure config\.ini and ztest\.csv have root read/write permissions

4. Linux environment: Double\-check Modbus port configuration

### 7\.2 Common Fault Troubleshooting Table

|Fault Symptom|Possible Root Cause|Solution|
|---|---|---|
|Failed PLC connection|Incorrect PLC IP / Network unreachable|Verify PLC IP list in config\.ini, test ping connectivity|
|Protocol client cannot connect|Port occupied by another process|Modify corresponding protocol port in config\.ini and restart the tool|
|All read values empty|Incorrect point config \(DB block / offset mismatch\)|Cross\-check CSV point mapping against actual TIA Portal PLC tags|
|MQTT connection failure|Wrong MQTT Broker IP / Port|Validate MQTT broker parameters and network reachability|
|Repeated PLC disconnections|Target DB block does not exist / Invalid point config|Check all tag definitions in CSV point file|

## 8\. Version Changelog

|Version|Release Date|Core Updates|
|---|---|---|
|v3\.5\.11|2026\-07\-31|Fixed decimal display bug in charts; fixed abnormal data filtering over 80 fields; optimized Modbus \& IEC104 write logic|
|v3\.5\.10|2026\-07\-28|Added SQLite query \& export functions; fixed cumulative device instance bug after remote restart; optimized log print formatting|
|v3\.5\.9|2026\-07\-25|Optimized Modbus write logic|
|v3\.5\.8|2026\-07\-12|Added point data archiving based on SQLite database|
|v3\.5\.6|2026\-07\-10|Lightweight refactoring via Doubao AI; fixed signed value display bug on IEC104 write; fixed infinite loop bug for invalid tags/PLC names via MQTT \& Web API|
|v3\.5\.5|2026\-03\-21|Added timeout retransmission with retry limit for PLC write commands via converted protocols; updated python\-snap7 to v3\.0\.0|
|v3\.5\.4|2025\-12\-29|AI refactor of custom MQTT/Modbus/IEC104/Web API modules, structural optimization and full re\-testing|
|v3\.5\.3|2025\-12\-09|Fixed auto\-start boot error on Linux; auto\-start function only available on Windows|
|v3\.5\.2|2025\-11\-08|Added read/write support for M area data \(set DB block number to 0 for M area access\)|
|v3\.5\.1|2025\-11\-05|Unified all point data type strings to lowercase|
|v3\.5\.0|2025\-07\-02|Deprecated modbus\-tk library; newly built Modbus TCP server, fully rewritten Modbus interaction logic|

## 9\. Disclaimer \& Contact Information

### 9\.1 Open Source Statement

This tool is open\-sourced under the MIT License and may be freely used for commercial and non\-commercial projects, provided original copyright notices are retained\. Third\-party libraries \(snap7, paho\-mqtt, etc\.\) used in the tool are governed by their respective open\-source licenses\.

### 9\.2 Contact Details

- Author: LinJiefeng \(Industrial Automation Expert\)

- Email: [i@icssteam\.cn](mailto:i@icssteam.cn)

- Project Repository: [https://gitee\.com/icsteam/siemens\-s7\-convert](https://gitee.com/icsteam/siemens-s7-convert)

- Reference Tools:

    - Snap7 Official Website: [https://snap7\.sourceforge\.net/](https://snap7.sourceforge.net/)

    - Eclipse Paho: [https://eclipse\.dev/paho/](https://eclipse.dev/paho/)

    - S7 Protocol Debug Tool: [https://blog\.csdn\.net/weixin\_44112083/article/details/130627005](https://blog.csdn.net/weixin_44112083/article/details/130627005)

    - IEC104 Client Debug Tool: [https://www\.redisant\.cn/iec104client](https://www.redisant.cn/iec104client)

---

### Translation Notes

1. Industry\-specific terminology follows international automation standards \(PLC, DB block, Modbus TCP, IEC104, Snap7, MQTT, TIA Portal, SCADA, MES\)

2. Code/config snippets, URLs and version numbers remain unchanged; only descriptive text localized

3. Chinese project nicknames and author aliases retained with English supplementary explanation

4. Variable names, file names and protocol keywords kept original to avoid confusion during deployment

5. Windows/Linux platform differentiation and industrial troubleshooting logic fully preserved

> （注：部分内容可能由 AI 生成）
