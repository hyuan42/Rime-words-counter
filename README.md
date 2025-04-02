# 免责免喷声明XD
#### 我是谁
首先，我并不是程序员，而是一名设计师，完全0代码经验。前段时间为了做这个脚本，才接触第一门编程语言——Python，而且只看到第五课"循环结构"，代码知识可以等同于0。  
本项目所有代码，皆由我一点一点通过DeepSeek修改、调整、拼凑出来的（非常的草台班子）。  
为什么把这个放在最前面跟大家说呢？因为如果大家运行时出了什么BUG，我大概率不知道怎么修复🤣。只能劳烦大家根据自身的设备环境，直接上传脚本文件+发送错误代码到DeepSeek或者其他AI去问啦——我也是这么做的。  
感谢DeepSeek，让以前不可能的事情变得可能，让0经验的我也能过一把程序员的瘾~~指Ctrl C + Ctrl V，哈哈~~

# Rime-words-counter简介
Rime-words-counter是一款用于rime输入法进行统计字数的脚本，按天/月/年/总这几个时间维度统计历史数据，同时可以进行输入测速等功能。  
该脚本并非由rime内部引擎实现的，而是借由python实现，因此需要电脑安装python环境以及正确安装依赖的库。  
#### 功能特性
✅统计本日字数，显示在状态栏/悬浮窗口；  
✅按"总-年-月-天”四个维度将历史数据归类，通过GUI界面进行查看；  
✅实时测速；

# 效果预览
#### Mac端 - 鼠须管
在系统顶部状态栏 (menus bar)显示今日输入的字数，可以打开详细数据，进行测速、查看历史数据等操作。  
![image](https://github.com/hyuan42/Rime-words-counter/blob/main/%E6%BC%94%E7%A4%BAGIF%E5%9B%BE/Mac%E6%BC%94%E7%A4%BA.gif?raw=true)

#### Windows端 - 小狼毫
在桌面生成一个悬浮窗口，显示今日输入的字数。可切换关闭/显示该悬浮窗口，可通过系统托盘图标打开详细数据，进行测速、查看历史数据等操作。  
![image](https://github.com/hyuan42/Rime-words-counter/blob/main/%E6%BC%94%E7%A4%BAGIF%E5%9B%BE/win%E6%BC%94%E7%A4%BA.gif?raw=true)

#### 查看历史数据记录
![image](https://github.com/hyuan42/Rime-words-counter/blob/main/%E6%BC%94%E7%A4%BAGIF%E5%9B%BE/%E5%8E%86%E5%8F%B2%E8%AE%B0%E5%BD%95.gif?raw=true)

# 版本说明
本脚本实现原理是——通过lua记录上屏的文字/字数到csv本地文档，再用Python脚本来处理数据。因此，本项目共提供两个版本：明文版&字数版。最后再根据鼠须管、小狼毫进行区分。
#### 明文版
本地生成的csv文档会将上屏的字数+文本都记录下来(适合文字工作者？例如在编辑过程文档丢失，通过这个文档把某个时间段输入的文本都找回来，或者适合想具体知道某个时间点打了什么字的人)。
#### 字数版 
本地生成的csv文档只记录上屏的字数，不记录明文，隐私性更好。

> 备注：多设备使用时，不同的版本不影响字数统计。即，你可以在公司电脑用字数版，在家里电脑用明文版。
#### 两者的csv文件对比示例
![image](https://github.com/hyuan42/Rime-words-counter/blob/main/%E6%BC%94%E7%A4%BAGIF%E5%9B%BE/Pasted%20image%2020250331195513.png?raw=true)

#### 脚本工作过程
当words_counter. lua被Rime输入法正确调用时，会在py_wordscounter文件夹里生成一个命名为words_input.csv的文档，记录了每次打字上屏时的时间点、汉字个数、汉字明文 (如果是字数版，则只生成前两个)。  
通过Python脚本words_counter. py处理该csv文档，按天/月/年/总这四个时间维度统计字数，保存到words_count_history.json文档里。  
创建一个GUI界面，将json文档的统计数据在前端显示出来。  
打字上屏→监测到csv文档变化→统计新增数据累加到json→监测到json文档变化→将数据变化显示在前端。  
每天00:00自动清理csv文档，保障数据处理时的轻量化（定时清理的时间可按需修改天数，同时也提供手动清理的功能）。  
# 使用方法
运行本脚本需要你的电脑安装Python环境，同时——  
Windows依赖以下库：
```
pip install portalocker pystray pillow pywin32 watchdog schedule
```

macOS依赖以下库：
```
pip install rumps portalocker watchdog schedule
```

#### 操作步骤
请按需选择查看以下文档：
[多设备使用，有多设备同步输入数据需求](https://github.com/hyuan42/Rime-words-counter/blob/main/%E6%95%99%E7%A8%8B-%E6%93%8D%E4%BD%9C%E6%AD%A5%E9%AA%A4/%E5%A4%9A%E8%AE%BE%E5%A4%87%E4%BD%BF%E7%94%A8%EF%BC%8C%E6%9C%89%E5%90%8C%E6%AD%A5%E9%9C%80%E6%B1%82.md)  
[Windows单设备使用-小狼毫](https://github.com/hyuan42/Rime-words-counter/blob/main/%E6%95%99%E7%A8%8B-%E6%93%8D%E4%BD%9C%E6%AD%A5%E9%AA%A4/Windows%E5%8D%95%E8%AE%BE%E5%A4%87%E4%BD%BF%E7%94%A8-%E5%B0%8F%E7%8B%BC%E6%AF%AB.md)  
[macOS单设备使用-鼠须管](https://github.com/hyuan42/Rime-words-counter/blob/main/%E6%95%99%E7%A8%8B-%E6%93%8D%E4%BD%9C%E6%AD%A5%E9%AA%A4/macOS%E5%8D%95%E8%AE%BE%E5%A4%87%E4%BD%BF%E7%94%A8-%E9%BC%A0%E9%A1%BB%E7%AE%A1.md)

> 备注：如果想修改定时清理csv的天数，请在words_counter. py里搜索"自动清理csv文档"修改以下代码 
```
# 括号留空即每天清理一次，想要多少天即在()中填入你想要的天数
schedule.every (). day.at ("00:00"). do (clear_csv)
```


# 存在的一些问题or可优化项
#### 1、没有封装成更方便的可执行文件
我尝试过用pyinstaller封装，但是失败了。鉴于我0编程经验，能做到这一步已经很不容易，目前版本已经能满足我个人需求，就不折腾了。  
如果有路过的大佬帮忙看看，自然是极好的。
#### 2、LUA脚本只记录纯汉字，不支持记录标点符号
有记录标点符号的需求的，请把LUA文件和你的需求发给AI进行优化~
#### 3、自启动与后台运行
默认是需要依赖终端/cmd运行python脚本的，关闭终端/cmd会中止脚本运行。至于如何摆脱终端/cmd，请自行搜索适合你设备的方法吧。  
像我这样的小白，直接问DeepSeek吧，“我有一个python脚本，想要在macos/windows里摆脱终端/cmd运行，想要开启自启动以及在后台运行”，会给完整的操作步骤去实现的。
#### 4、多设备同步可能会遇到的情况
如果多设备没有同时在打字，比如白天在公司，晚上在家，始终只有一个设备在打字的话，可以忽略这点。  
但如果多设备同时在打字（白天你在公司，同时家里有人在用你电脑），由于两个设备同时有打字行为，会同时写入数据到json中，而同步盘没办法识别文件锁的状态，例如onedrive/iCloud等就会自动触发文件冲突的保护机制，导致同步盘中生成另一个按设备命名的json文件，需要手动去解决这些冲突文件。  
我不知道咋解决，再加上我个人没有多设备同时运行打字的场景，所以对我没什么影响，就不管了。
#### 5 、Windows小狼毫的悬浮弹窗层级问题
悬浮窗口等级比较高，全屏看视频的时候也会出现在最前方。这个没办法，更好的实现方式是像 [TrafficMonitor](https://github.com/zhongyang219/TrafficMonitor) 显示在任务栏上会更好，问了一下AI需要用到C语言，对我来说更超纲了，所以不折腾。
#### 6、macOS鼠须管的lua文件运行时似乎有小bug
有个奇怪的bug，部署words_counter.lua脚本并打字后，首次生成的csv是有表头的。如果把该首次生成的csv文件删除，重新打字后生成的就没有表头了。  
没有表头的话，python无法正确处理数据。  
解决方法：删除没有表头的csv，重新部署，再生成个新的就好；或者，手动添加表头。
