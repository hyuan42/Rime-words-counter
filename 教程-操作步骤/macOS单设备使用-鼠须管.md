#### 准备
请首先确定电脑是否都有python环境，且已经安装了依赖的库。
macOS依赖以下库：
```
pip install rumps portalocker watchdog schedule
```

#### 操作步骤
##### 第1步
在Releases中，选择鼠须管-字数/明文版，按需下载你要的版本，解压后得到py_wordscounter文件夹；
##### 第2步
点击顶部状态栏里的输入法，选择“用户设定…”打开用户文件夹，将py_wordscounter文件夹移到用户文件夹内；
##### 第3步
打开py_wordscounter文件夹，将words_counter. lua脚本移到用户文件夹的「lua」文件夹中，没有「lua」文件夹就手动新建文件夹并命名为「lua」；
##### 第4步
打开words_counter.lua，修改生成的csv文件的路径到py_wordscounter文件夹内；
```
macOS系统：
local csv_path = "/Users/你的设备名/Library/Rime/py_wordscounter/words_input.csv" -- 改为实际的CSV文件路径
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
修改words_counter.py脚本中CUSTOM_PATH和CUSTOM_PATH2的路径，用同个文件夹路径即可。
> CUSTOM_PATH：存放历史数据-json文档的路径。
> CUSTOM_PATH2：放置Python脚本和csv文档的本地路径，也就是py_wordscounter文件夹的路径。

```
示例：
CUSTOM_PATH = "/Users/你的设备名/Library/Rime/py_wordscounter"
CUSTOM_PATH2 = "/Users/你的设备名/Library/Rime/py_wordscounter"
```
打开status_bar_app. py，把上面修改好的CUSTOM_PATH和CUSTOM_PATH2直接复制粘贴过来。
即——words_counter. py和status_bar_app. py的路径要保持一致。

##### 第8步
修改完毕后，打开终端运行status_bar_app. py即可。
在正确设置的情况下，你的menus bar应该已经出现字数统计了，右键字数统计，可打开gui主页面查看详细信息和测速。

Enjoy it.

#### 关于自启动和后台静默运行
当前脚本默认需要依赖终端运行，关闭终端会中止脚本运行。至于如何摆脱终端，请自行搜索吧。  
像我这样的小白，直接问DeepSeek，“我有一个python脚本，想要在macos里摆脱终端，想要开启自启动以及在后台运行，要怎么是设置”，会给完整的操作步骤去实现的。

#### 备注
运行后，目录结构应该是这样的↓
```
Rime/
└── lua/
    └── words_counter.lua           #通过这个lua记录上屏数据
	
└── py_wordscounter/
    ├── status_bar_app.py           #把字数显示在状态栏
    ├── words_counter. py           #处理csv文档的数据的脚本+主页面GUI、测速等功能
    ├── words_input.csv             #保存lua记录的你上屏的数据
    └── words_count_history.json    #历史统计数据的文件
```
