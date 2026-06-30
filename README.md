

# 叠甲声明XD
我不是程序员，只是一名完全0代码经验的设计师，非常的草台班子。

本项目v1.0所有代码全由DeepSeek对话调整出来，v1.1（当前版本）借由cursor，用Claude实现。

为什么把这个放在最前面说呢？因为诸位运行时，如果出了什么BUG，我大概率也不知道咋回事🤣。只能劳烦大家根据自身的设备环境，上传脚本文件+发送错误代码到AI解决——我也是这么做的。

提issue也可以的，将运行日志的报错代码贴上来（不一定能修好就是了）。

# Rime-words-counter简介
Rime-words-counter是一款用于Rime输入法进行统计字数的脚本，按天/月/年/总这几个时间维度统计历史数据，亦可以进行输入测速。

本仓库提供Mac-鼠须管、Win-小狼毫的打包版本，开箱即用。

#### 功能特性

✅统计本日字数，显示在状态栏/悬浮窗口；

✅按"总-年-月-天”四个维度将历史数据归类，通过GUI界面进行查看；

✅实时测速；

# 效果预览
### Mac端 - 鼠须管
在系统顶部状态栏 (menus bar)显示今日输入的字数，可以打开详细数据，进行测速、查看历史数据等操作。

![image](https://github.com/hyuan42/Rime-words-counter/blob/main/预览图/Pasted%20image%2020260629154801.png?raw=true)
### Windows端 - 小狼毫
在桌面生成一个悬浮窗口，显示今日输入的字数。

同时在系统托盘生成图标 (蓝底+字的icon)，可通过图标切换显示悬浮窗口、打开详细数据，进行测速、查看历史数据等操作。

![image](https://github.com/hyuan42/Rime-words-counter/blob/main/预览图/Pasted%20image%2020260629141852.png?raw=true)
### 按年/月查看历史数据记录-趋势-热力图
![image](https://raw.githubusercontent.com/hyuan42/Rime-words-counter/refs/heads/main/%E9%A2%84%E8%A7%88%E5%9B%BE/Pasted%20image%2020260629141630.png)
![image](https://raw.githubusercontent.com/hyuan42/Rime-words-counter/refs/heads/main/%E9%A2%84%E8%A7%88%E5%9B%BE/Pasted%20image%2020260629154440.png)
### 明文版Vs字数版
默认字数版，可通过“设置”开启明文版。
- 字数版：只记录上屏的字数到csv，不记录明文，隐私性更好。
- 明文版：所有上屏文字都会被记录到本地csv。

> 注1：切换明文/字数版时，实际上是改动了Lua脚本，因此每次切换之后，需重新部署Rime输入法才能生效。
> 
> 注2：多设备使用时，不同的版本不影响字数统计。即，你可以在公司电脑用字数版，在家里电脑用明文版。

![image](https://raw.githubusercontent.com/hyuan42/Rime-words-counter/refs/heads/main/%E9%A2%84%E8%A7%88%E5%9B%BE/csv%E5%AF%B9%E6%AF%94.png)
# 功能说明
本脚本实现原理是——

通过Lua记录上屏的文本/字数到csv本地文档，再用Python脚本来处理数据，并创建GUI界面将该数据显示出来。
### 具体的工作过程
当本项目Lua脚本被Rime输入法正确调用时，会在Rime的用户文件夹中创建py_wordscounter文件夹，并生成一个csv的文档，用于记录每次打字上屏时的时间点、汉字个数、汉字明文 (如果是字数版，则只记录前两个)。

通过Python脚本处理该csv文档，按天/月/年/总这四个时间维度统计字数，汇总保存到history.json文档里。

创建一个GUI界面，将json文档的统计数据在前端显示出来。

流程：

打字上屏→Lua脚本采集进csv文档→Python统计新增数据累加到json文档→将json的数据变化显示在前端。

每天00:00自动清理csv文档，保障数据处理时的轻量化（定时清理的时间间隔可按需自定义，同时也提供手动清理的功能）。
# 使用方法
### Step1.下载
在 [Release](https://github.com/hyuan42/Rime-words-counter/releases) 中，按需下载鼠须管或者小狼毫版本，解压。
### Step2. 配置Lua脚本
打开文件夹，将words_counter.lua脚本移动到Rime的「用户文件夹」的「lua」文件夹中，没有「lua」文件夹就手动新建文件夹并命名为「lua」。

打开“你的配置方案.schema.yaml”，在processors下添加"lua_processor@* words_counter"；

```
示例：
engine:
  processors:
    - 其他lua...   
    - lua_processor@*words_counter #新增这个，星号*的后面不要带空格
```

保存.yaml后，点击Rime输入法-重新部署，让该lua脚本生效。

打字，看用户文件夹内是否自动生成py_wordscounter，且文件夹内是否生成words_input.csv文件，且表头和列表数据都正常。
### Step3. 使用
双击字数统计.app或字数统计.exe打开软件，软件将自动识别csv文档并开始工作。建议：可将该软件设置为开机自启动。

软件开始工作后，会生成配置文件与最重要的history.json文档，该json文档将记录你每天的字数汇总，可定时备份该文档以防数据丢失。

可通过【菜单栏-配置文件夹】打开并查看json，或通过【菜单栏-设置】修改该json的位置。
### Step4. 多设备使用，云盘同步
如果你是多设备，想把同一天内多个设备的字数都汇总统计，可在【设置】中，将历史history.json的路径修改到云盘内，并按需修改每个设备的名字（设备不能同名）。
#### 多设备同步注意事项
##### ⚠️一、使用A设备一段时间后，增加设备B同步时
多设备修改到同一路径时，history. json文件是覆盖行为。即，将新设备B的路径修改到云盘的时候，设备B的新json可能会覆盖旧json，导致旧数据丢失。

✅Do：同步前，先备份云盘中的旧json，再执行B设备修改路径的操作。

若过往数据被覆盖，则：

- （推荐）将新json中包含了多个"devices"的字段替换到旧json文件里。
- 或者，将json备份中的"daily、monthly、yearly、total"数据替换进新的json文件里；
##### ⚠️二、有两份不同的JSON
如果A、B设备各自运行了一段时间，有2份不同的JSON，这时才想合并——

✅Do：同步前，将两份文件发给ai工具，让ai帮你将两份文件合并成一份，同一日期的字数相加即可。随后再将该合并版的JSON放到云盘 (文件命名记得改回words_count_history.json)；

##### ⚠️三、多设备同时打字写入JSON
如果多设备没有同时在打字，比如白天在公司，晚上在家，始终只有一个设备在打字的话，可以忽略这点。

但如果多设备同时在打字（白天你在公司，同时家里有人在用你电脑），由于两个设备同时有打字行为，且刚好同时在写入JSON，同步盘会无法识别文件锁的状态，例如onedrive/iCloud等会触发文件冲突的保护机制，导致同步盘中生成另一个按设备命名的json文件，需要手动去解决这些冲突文件。

# 其他
#### 1、LUA脚本只记录纯汉字，不支持记录标点符号
有记录标点符号的需求的，请把LUA文件和你的需求发给AI进行优化~
#### 2 、Windows小狼毫的悬浮弹窗层级问题
悬浮窗口等级比较高，全屏看视频的时候也会出现在最前方。这个没办法，更优雅的实现方式是像 [TrafficMonitor](https://github.com/zhongyang219/TrafficMonitor) 显示在任务栏上。但折腾起来太麻烦了，放弃。
#### 3、用obsidian来统计数据，无需下载软件
详情可以参考该[issue#1](https://github.com/hyuan42/Rime-words-counter/issues/1)中提供的方法。按该方法，仅需下载本项目中的Lua脚本即可~
#### 4、若不想用软件版，想用Python脚本
请根据设备下载对应的版本的源文件，pip requirements.txt安装依赖后——

👉win版运行words_counter. py

👉mac版运行status_bar_app.py


