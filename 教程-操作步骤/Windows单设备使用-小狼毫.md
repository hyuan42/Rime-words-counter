#### 准备
请首先确定电脑是否都有python环境，且已经安装了依赖的库。  
Windows依赖以下库：
```
pip install portalocker pystray pillow pywin32 watchdog schedule
```

#### 操作步骤
##### 第1步
在Releases中，选择小狼毫-字数/明文版，按需下载你要的版本，解压后得到py_wordscounter文件夹；
##### 第2步
右键输入法，选择“用户文件夹”打开，将py_wordscounter文件夹移到用户文件夹内；
##### 第3步
打开py_wordscounter文件夹，将words_counter. lua脚本移到用户文件夹的「lua」文件夹中，没有「lua」文件夹就手动新建文件夹并命名为「lua」；
##### 第4步
打开words_counter.lua，修改生成的csv文件的路径到py_wordscounter文件夹内；
```
win系统：
注意，win系统的lua文件的路径需要用两个反斜杠
local csv_path = "C:\\Users\\电脑设备名\\AppData\\Roaming\\Rime\\py_wordscounter\\words_input.csv" -- 改为实际的CSV文件路径，

```
##### 第5步
打开“你的配置方案.schema.yaml”，在processors下添加"lua_processor@* words_counter"；
```
示例：
engine:
  processors:
    - 其他lua...   
    - lua_processor@*words_counter #新增这个，星号后面不要带空格
```
##### 第6步
保存以上两个文件的修改后，点击rime输入法-重新部署，让该lua脚本生效。打字，看py_wordscounter文件夹内是否生成words_input.csv文件，且表头和列表数据都正常。
##### 第7步
打开words_counter. py，修改CSV_FILE 和JSON_FILE，用同个文件夹路径即可。  
> CSV_FILE：存放lua脚本生成的csv文档的路径，py脚本需要读取这个文档来处理数据；  
> JSON_FILE：生成&读取统计后的字数数据以及保存历史数据的文档。
```
示例：
CSV_FILE = r'C:\Users\电脑设备名\AppData\Roaming\Rime\py_wordscounter\words_input.csv'
JSON_FILE = r'C:\Users\电脑设备名\AppData\Roaming\Rime\py_wordscounter\words_count_history.json'
```
##### 第8步
修改完毕后，运行words_counter. py即可。  
在正确设置的情况下，你的桌面右下角应该已经出现字数统计的悬浮窗口，系统托盘出现蓝色icon的“字”图标。  
悬浮窗口等级比较高，全屏看视频的时候也会出现在最前方，忍不了的可以X关掉。关掉后可通过系统托盘“切换悬浮窗”再调出来。  
右键系统托盘icon，可打开gui主页面查看详细信息、历史记录和测速。  
  
Enjoy it.

#### 关于自启动和后台静默运行
当前脚本默认需要依赖cmd运行，关闭cmd会中止脚本运行。至于如何摆脱终端，请自行搜索吧。  
像我这样的小白，直接问DeepSeek，“我有一个python脚本，想要在win10里摆脱cmd运行，想要开启自启动以及在后台静默运行，要怎么设置”，会给完整的操作步骤去实现的。  
我自己是用pythonw.exe+快捷方式，再把这个快捷方式扔到开机自启动文件夹的形式实现的。  
详细的操作步骤问AI吧，这里不多赘述。  
#### 备注
运行后，目录结构应该是这样的↓
```
Rime/
└── lua/
    └── words_counter.lua           #通过这个lua记录上屏数据
	
└── py_wordscounter/
    ├── words_counter. py           #处理csv文档的数据的脚本+主页面GUI+测速+创建系统托盘等功能
    ├── words_input.csv             #保存lua记录的你上屏的数据
    └── words_count_history.json    #历史统计数据的文件
```
