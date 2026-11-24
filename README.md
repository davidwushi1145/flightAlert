# flightAlert：机票价格提醒工具

flightAlert 定期查询携程低价日历。价格变化达到设定阈值时，程序通过 PushPlus 发送微信通知。你可以使用图形界面，也可以在命令行运行。

程序使用携程移动端 `15380` 接口。显示、比较和通知均采用 `totalPrice` 含税价，币种为人民币（CNY）。日历报价可能存在缓存，实际购买价以订单页为准。

## 功能

- 监控多个出发日期。
- 查询国内、国际航线，选择舱等。
- 查询单程或固定返程日期的往返报价。
- 首次取得有效报价时发送通知。
- 涨跌达到阈值时发送通知，成功后更新比较基准。
- 保留失败通知，在后续查询轮次补发。
- 跨日后自动跳过过期日期，继续监控剩余日期。
- 保留缺失日期的状态，避免误报降价。

## 快速开始

### 安装依赖

运行环境需要 Python 3.10 或更高版本。图形界面还需要 Tkinter。

下载项目并进入目录：

```bash
git clone https://github.com/davidwushi1145/flightAlert.git
cd flightAlert
```

在 macOS 或 Linux 上创建虚拟环境：

```bash
python3 -m venv venv
source venv/bin/activate
```

在 Windows PowerShell 中创建虚拟环境：

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

激活环境后，安装依赖：

```bash
python -m pip install -r requirements.txt
```

### 启动图形界面

```bash
python flight_alert_gui.py
```

1. 打开“配置设置”，填写出发日期和城市代码。
2. 选择航线范围、航程类型和舱等。
3. 往返查询需填写固定返程日期。
4. 设置检查间隔和价格变动阈值。
5. 按需填写 PushPlus 推送令牌，保存配置。
6. 打开“价格监控”，点击“开始监控”。

界面显示含税报价、采集时间和缺失日期。停止监控或关闭窗口时，程序先等待当前请求结束。修改配置后，请重新启动监控。

### 使用命令行

先编辑项目目录下的 `config.json`，再启动监控：

```bash
python flight_alert.py
```

指定配置文件：

```bash
python flight_alert.py --config /path/to/config.json
```

仅查询一轮，并关闭本次运行的通知：

```bash
python flight_alert.py --once --no-notify
```

单次查询全部目标日期有报价，且已启用的通知发送成功时，退出码为 `0`。缺少报价、查询失败或通知失败时，退出码为 `1`。`--no-notify` 模式只检查查询结果。

## 配置

### 配置示例

以下示例监控昆明至济南的单程经济舱报价。

```json
{
  "dateToGo": ["20261105", "20261106"],
  "placeFrom": "KMG",
  "placeTo": "TNA",
  "flightWay": "Oneway",
  "searchType": 1,
  "grade": 1,
  "returnDate": "",
  "passengerList": [
    {"passengerType": "Adult", "passengerCount": 1}
  ],
  "sleepTime": 600,
  "priceStep": 50,
  "SCKEY": ""
}
```

| 配置项 | 说明 |
| --- | --- |
| `dateToGo` | 出发日期列表。接受 `YYYYMMDD` 或 `YYYY-MM-DD`，保存时统一为前者 |
| `placeFrom` | 出发城市代码，如 `KMG` |
| `placeTo` | 到达城市代码，如 `TNA` |
| `flightWay` | `Oneway` 为单程，`Roundtrip` 为往返。默认单程 |
| `searchType` | `1` 为国内，`2` 为国际。默认 `1` |
| `grade` | 舱等编码。默认 `1`，取值见下表 |
| `returnDate` | 固定返程日期。往返必填，日期须晚于或等于所有出发日期 |
| `passengerList` | 乘客类型和人数。默认 1 位成人 |
| `sleepTime` | 正常查询间隔，单位为秒。须为正整数，默认 `600` |
| `priceStep` | 价格变动阈值，单位为元。须为正整数，默认 `50` |
| `SCKEY` | PushPlus 推送令牌。留空时仅记录价格 |

舱等编码如下：

| 编码 | 舱等 |
| --- | --- |
| `1` | 经济舱 |
| `2` | 超级经济舱 |
| `3` | 经济舱和超级经济舱 |
| `4` | 商务舱 |
| `8` | 头等舱 |
| `12` | 商务舱和头等舱 |

### 往返和乘客配置

往返查询使用一个固定返程日期。程序将每个出发日期与返程日期配对。例如，出发日期为 11 月 5 日和 6 日，返程日期为 11 月 12 日，程序分别查询这两组往返报价。

乘客类型包括 `Adult`（成人）、`Child`（儿童）和 `Baby`（婴儿）。每项使用 `passengerCount` 指定人数。国内查询固定为 1 位成人，国际查询至少需要 1 位成人。

上海至香港等航线使用国际模式，即 `searchType=2`。国际往返日历可能缺少部分日期组合，程序会显示“暂无有效含税报价”。

### 配置保存位置

| 运行方式 | 默认配置位置 |
| --- | --- |
| Windows 图形界面 | `%APPDATA%/flightAlert/config.json` |
| macOS、Linux 图形界面 | 源码或可执行文件所在目录下的 `config.json` |
| 命令行 | `flight_alert.py` 所在目录下的 `config.json` |

命令行可通过 `--config` 读取其他位置的配置。推送令牌保存在配置文件中，内容为明文。输入框的遮蔽仅用于防止旁人查看屏幕。

图形界面先完整校验配置，再填充输入框。未知的航线范围和舱等会报错。过期日期可以加载和保存，便于修改；开始监控时须使用有效日期。

保存配置时，程序先写入同目录的临时文件，再替换原文件。写入或替换失败时，原配置保持完整。

### 运行状态与日志

运行期间，程序按中国时区检查日期。某个出发日期过期后，程序继续查询其他日期。全部日期过期时，监控自动结束。

连续监控时，连接失败、请求超时和 HTTP `5xx` 错误会在 30 秒后重试。HTTP `429` 按有效的 `Retry-After` 等待，缺少有效值时使用配置的 `sleepTime`。`--once` 遇到这些错误时记录失败并以状态码 1 退出。配置错误、业务错误或响应格式变化会停止监控。请查看错误信息，处理后重新启动。

命令行和图形界面均写入 `flight_alert.log`。日志保存在当前配置文件所在目录。单个文件达到约 2 MB 时轮换，最多保留 3 个备份。图形界面的活动日志会显示文件位置和通知失败原因。

## 携程日历接口

### 请求方式

接口按日期返回航线低价。请求使用 `POST` 方法，参数放在 JSON 请求体中。

```text
https://m.ctrip.com/restapi/soa2/15380/bjjson/FlightIntlAndInlandLowestPriceSearch
```

程序模拟 Android 微信小程序的请求头。命令行和图形界面共用此配置：

```http
Content-Type: application/json
Accept: */*
Referer: https://servicewechat.com/wx0e6ed4f51db9d078/1022/page-frame.html
x-ctx-currency: CNY
x-ctx-locale: zh-CN
x-ctx-region: CN
x-ctx-group: ctrip
User-Agent: Mozilla/5.0 (Linux; Android 14; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/112.0.5615.136 Mobile Safari/537.36 MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN miniProgram/wx0e6ed4f51db9d078
```

请求头定义在 [ctrip_headers.py](ctrip_headers.py)。

### 请求参数

程序根据监控配置生成请求参数。配置文件与接口请求体使用不同的字段名称。

| 接口参数 | 程序取值 |
| --- | --- |
| `searchType` | 读取同名配置：国内为 `1`，国际为 `2` |
| `departNewCityCode` | 读取 `placeFrom` |
| `arriveNewCityCode` | 读取 `placeTo` |
| `startDate` | 从 `dateToGo` 选取出发日期，转换为 `YYYY-MM-DD` |
| `returnDate` | 往返时读取同名配置，转换为 `YYYY-MM-DD` |
| `passengerList` | 读取同名配置，仅保留人数大于 `0` 的项 |
| `grade` | 读取同名配置 |
| `calendarSelections` | 根据舱等生成。国际查询另加含税显示条件 |
| `flag` | 根据航线范围和航程类型生成，取值见下表 |
| `channelName` | 固定为 `"MobileH5"` |
| `lowPriceExtendInfo` | 固定为空数组 `[]` |
| `head` | 程序省略此字段 |

航程参数组合如下：

| 查询场景 | `searchType` | `flag` | `returnDate` |
| --- | --- | --- | --- |
| 国内单程 | `1` | `0` | 省略 |
| 国际单程 | `2` | `1` | 省略 |
| 国内往返 | `1` | `4` | 填写返程日期 |
| 国际往返 | `2` | `5` | 填写返程日期 |

舱等同时写入 `grade` 和 `calendarSelections`。例如，经济舱使用以下参数：

```json
{
  "grade": 1,
  "calendarSelections": [
    {"selectionType": 8, "selectionContent": ["1"]}
  ]
}
```

国际查询会追加含税显示条件：

```json
{"selectionType": 2, "selectionContent": ["1"]}
```

### 响应与价格处理

程序先检查业务状态，再读取 `priceList`。HTTP 状态码为 `200`，只表示请求在 HTTP 层成功。

| 响应字段 | 用途 |
| --- | --- |
| `responseStatus.Ack` | 须为 `"Success"` |
| `responseStatus.Errors` | 检查业务错误 |
| `priceList[].departDate` | 匹配出发日期 |
| `priceList[].returnDate` | 往返时匹配返程日期 |
| `priceList[].totalPrice` | 作为含税报价，用于显示、比较和通知 |
| `priceList[].price` | 保留在接口响应中，程序使用 `totalPrice` 比较价格 |

日期可能采用 `/Date(毫秒时间戳+0800)/` 格式。程序按中国时区转换日期，避免跨日偏差。

单程每轮请求一次日历，再筛选目标日期。往返每轮按出发日期分别请求，同时匹配出发日期和返程日期。`startDate` 不严格限定返回范围，因此程序始终检查响应日期。

价格为 `0`、字段缺失或日期未匹配时，程序保留缺失状态。这些情况不代表免费机票或确定无航班。缺失报价不会作为新价格参与比较。

### 适用范围

本项目已取得国内单程、国内往返和国际单程报价。具体航线和日期的覆盖情况，以查询结果为准。

## 通知规则

程序首次取得有效报价时发送通知。PushPlus 确认成功后，程序建立比较基准。之后，价格涨跌达到 `priceStep` 时，程序再次通知。发送成功后才更新基准。较小的价格变化会继续相对该基准累计。

例如，基准为 630 元，阈值为 50 元。价格降至 610 元时，基准仍为 630 元。价格降至 580 元时，程序通知降价并更新基准。

同一查询轮次内触发的多个日期变化按出发日期升序合并为一条 PushPlus 通知，各自保留报价采集时间。发送成功后确认整批基准，失败则整批保留待下轮重新判断。价格变化仍达到阈值时补发；价格回到阈值内时，取消旧提醒。

某个日期缺少新报价时，程序可补发此前的失败通知。通知包含原报价的采集时间。日期过期后，程序清除对应的待发送通知。

未填写令牌时，程序仅记录价格，并按阈值更新本地基准。比较基准和待发送状态均保存在内存中，每次启动监控都会重新建立。PushPlus 已接收请求但响应丢失时，重试可能产生重复通知。

填写 `SCKEY` 可启用微信通知，令牌申请方式见 [PushPlus 文档](https://www.pushplus.plus/doc/)。

## 常用城市代码

以下代码用于指定城市。部分城市代码与单个机场代码不同。

| 城市 | 代码 | 城市 | 代码 |
| --- | --- | --- | --- |
| 北京 | `BJS` | 上海 | `SHA` |
| 广州 | `CAN` | 深圳 | `SZX` |
| 成都 | `CTU` | 杭州 | `HGH` |
| 武汉 | `WUH` | 西安 | `SIA` |
| 重庆 | `CKG` | 青岛 | `TAO` |
| 长沙 | `CSX` | 南京 | `NKG` |
| 厦门 | `XMN` | 昆明 | `KMG` |
| 济南 | `TNA` | 福州 | `FOC` |
| 南昌 | `KHN` | 香港 | `HKG` |

## 开发与构建

### 模块职责

| 模块 | 职责 |
| --- | --- |
| `flight_alert.py` | 命令行参数、启动与退出 |
| `flight_alert_gui.py` | 表单、界面事件和窗口生命周期 |
| `monitoring.py` | 监控调度、日期过期、价格比较和通知确认 |
| `flight_api.py` | 携程请求与含税报价解析 |
| `settings.py` | 配置校验、规范化和原子保存 |
| `notifications.py` | PushPlus 请求及发送结果 |
| `app_logging.py` | 两种入口共用的文件日志 |

两个入口共用监控服务。后台线程通过队列提交事件，由 GUI 主线程更新界面。监控服务支持注入取价函数、通知函数和时钟，便于离线测试。

### 运行测试

```bash
python -m unittest discover -s tests -v
```

测试使用模拟响应，不查询真实票价，也不发送通知。测试覆盖通知补发、跨日运行、配置保存失败和界面关闭等场景。GitHub Actions 在代码推送和拉取请求中运行测试。

### 构建 Windows 应用

在 Windows 环境中安装 PyInstaller 并执行构建：

```powershell
python -m pip install pyinstaller
pyinstaller --onefile --windowed --name FlightAlert flight_alert_gui.py
```

生成文件位于 `dist/FlightAlert.exe`。标签发布流程也会先运行测试，再构建 Windows 应用。

## 项目来源与使用说明

**本程序仅供个人学习和研究，禁止用于商业用途。请遵守相关法律法规与服务条款。**
