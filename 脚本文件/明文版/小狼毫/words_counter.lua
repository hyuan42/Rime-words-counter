--[[

版本: 字数统计工具-小狼毫明文版1.0
作者: hyuan
Github:https://github.com/hyuan42/Rime-words-counter

脚本功能：将输入法上屏的汉字个数+明文文本一起记录到一个本地的csv文档中，方便后续Python脚本统计、处理数据。
注意1，本脚本只筛选纯汉字，字符、标点符号不在统计范围内。想要统计字符、标点符号，请把本脚本传DeepSeek+说明你的需求，让DeepSeek优化看看吧。
注意2，本脚本会记录上屏文本到csv文档中，介意隐私问题的请使用字数版。

--]]

local csv_path = "C:\\Users\\你的用户名\\AppData\\Roaming\\Rime\\py_wordscounter\\words_input.csv" -- 改为实际的CSV文件路径，注意，win系统这里需要用两个\\，不要修改csv的文件名，改前面的文件夹路径就好。注意，要跟words_counter.py的CSV_FILE路径一致


-- 判断文本是否包含至少一个汉字
function is_valid_text(text)
    for _, c in utf8.codes(text) do
        if c >= 0x4E00 and c <= 0x9FFF then
            return true
        end
    end
    return false
end

-- 获取当前时间戳（格式：YYYY-MM-DD HH:MM:SS）
function get_timestamp()
    return os.date("%Y-%m-%d %H:%M:%S")
end

-- 计算文本中的汉字个数
function count_chinese_characters(text)
    local count = 0
    for _, c in utf8.codes(text) do
        if c >= 0x4E00 and c <= 0x9FFF then
            count = count + 1
        end
    end
    return count
end

-- 获取上屏的文本，将文本计算个数后一起写入csv文档
function on_commit(context)
    local commit_text = context:get_commit_text()
    
    if is_valid_text(commit_text) then
        local chinese_count = count_chinese_characters(commit_text)
        local file, err = io.open(csv_path, "a")
        if file then
            -- CSV 格式：时间戳,汉字个数
            local csv_line = string.format(
                '"%s","%d","%s"\n',
                get_timestamp(),
                chinese_count,
                commit_text:gsub('"', '""')
            )
            file:write(csv_line)
            file:close()
        else
            log.error("无法写入CSV文件: " .. err)
        end
    end
end


function inite(env)
    env.engine.context.commit_notifier:connect(on_commit)
    -- 初始化CSV文件（如果不存在）
    local header_file = io.open(csv_path, "r")
    if not header_file then
        header_file = io.open(csv_path, "w")
        if header_file then
            header_file:write('"timestamp","chinese_count","text"\n')
            header_file:close()
        end
    end
end

return { init = inite }