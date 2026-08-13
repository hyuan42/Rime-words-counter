--[[

版本: 字数统计工具 v1.1（Weasel 0.17.x 兼容修复版）
作者: hyuan
Github仓库: https://github.com/hyuan42/Rime-words-counter
原版时间: 2026-06-26
修复时间: 2026-08-13

脚本功能：将输入法上屏的汉字个数和时间戳追加到本地 CSV。

开启明文模式，把 ENABLE_PLAINTEXT 改成 true。

修复说明：
1. CSV 直接放在 Rime 用户目录根部，不再创建额外子目录。
2. 初始化阶段不再调用 os.execute，避免重新部署时阻塞 Weasel。
3. Writer 只打开一次文件，不再先读、再写、再追加地重复打开。
4. notifier 连接保存在 env 中，并在 fini 时主动断开。
5. 上屏时不再额外打开 CSV 检查文件是否存在。

--]]

local CUSTOM_CSV_PATH  = nil    -- 自定义路径，nil = Rime 用户目录/words_input.csv
local ENABLE_PLAINTEXT = false  -- true = 第三列记录上屏原文

local M = {}
local CSV_HEADER = "timestamp,chinese_count,text\n"
local MAX_BUFFER_SIZE = 1000

-- ============ CJK 区段（按 Unicode 14.0） ============
local CJK_RANGES = {
    {0x3400,  0x4DBF },   -- CJK Unified Ideographs Extension A
    {0x4E00,  0x9FFF },   -- CJK Unified Ideographs
    {0xF900,  0xFAFF },   -- CJK Compatibility Ideographs
    {0x20000, 0x2A6DF},   -- CJK Extension B
    {0x2A700, 0x2EBEF},   -- CJK Extension C / D / E / F
    {0x2F800, 0x2FA1F},   -- CJK Compatibility Supplement
    {0x30000, 0x323AF},   -- CJK Extension G / H
}

local function is_cjk(c)
    for i = 1, #CJK_RANGES do
        local r = CJK_RANGES[i]
        if c >= r[1] and c <= r[2] then return true end
    end
    return false
end

local function count_cjk(text)
    local n = 0
    for _, c in utf8.codes(text) do
        if is_cjk(c) then n = n + 1 end
    end
    return n
end

local function get_timestamp()
    return os.date("%Y-%m-%d %H:%M:%S")
end

-- ============ 路径解析 ============
local function detect_platform()
    return package.config:sub(1, 1) == "\\" and "windows" or "unix"
end

local function path_join(dir, filename)
    if not dir or dir == "" then return filename end
    local last = dir:sub(-1)
    if last == "/" or last == "\\" then
        return dir .. filename
    end
    local sep = detect_platform() == "windows" and "\\" or "/"
    return dir .. sep .. filename
end

local function rime_user_dir()
    -- librime-lua 新版接口；兼容函数式和方法式两种绑定。
    if rime_api and rime_api.get_user_data_dir then
        local ok, dir = pcall(rime_api.get_user_data_dir)
        if (not ok or not dir or dir == "") then
            ok, dir = pcall(function() return rime_api:get_user_data_dir() end)
        end
        if ok and dir and dir ~= "" then
            return dir
        end
    end

    -- 旧版接口兜底。Weasel 默认目录为 %APPDATA%\\Rime。
    if detect_platform() == "windows" then
        local appdata = os.getenv("APPDATA") or "C:\\"
        return path_join(appdata, "Rime")
    end

    local home = os.getenv("HOME") or ""
    return path_join(path_join(home, "Library"), "Rime")
end

local function default_csv_path()
    if CUSTOM_CSV_PATH and CUSTOM_CSV_PATH ~= "" then
        return CUSTOM_CSV_PATH
    end
    return path_join(rime_user_dir(), "words_input.csv")
end

-- ============ CSV 字段转义 ============
local function csv_escape(s)
    if s:find('[,"\n\r]') then
        return '"' .. s:gsub('"', '""') .. '"'
    end
    return s
end

-- ============ 写入器：单一常驻句柄 + 有界失败缓冲 ============
local Writer = {}
Writer.__index = Writer

function Writer.new(path, plaintext)
    local self = setmetatable({}, Writer)
    self.path = path
    self.plaintext = plaintext
    self.handle = nil
    self.buffer = {}
    self:open()
    return self
end

function Writer:open()
    if self.handle then return true end

    -- a+ 会在文件不存在时创建文件；父目录必须已经存在。
    -- CSV 位于 Rime 用户目录根部，因此无需在 Lua 中创建目录。
    local f, err = io.open(self.path, "a+")
    if not f then
        if log and log.error then
            log.error("无法打开字数统计 CSV: " .. tostring(self.path) .. "; " .. tostring(err))
        end
        return false
    end

    f:setvbuf("line")

    local size, seek_err = f:seek("end")
    if not size then
        f:close()
        if log and log.error then
            log.error("无法定位字数统计 CSV: " .. tostring(seek_err))
        end
        return false
    end

    if size == 0 then
        local ok, write_err = f:write(CSV_HEADER)
        if not ok then
            f:close()
            if log and log.error then
                log.error("无法写入 CSV 表头: " .. tostring(write_err))
            end
            return false
        end
        f:flush()
    end

    self.handle = f
    return true
end

function Writer:close()
    if self.handle then
        pcall(function() self.handle:close() end)
        self.handle = nil
    end
end

function Writer:buffer_line(line)
    if #self.buffer < MAX_BUFFER_SIZE then
        self.buffer[#self.buffer + 1] = line
    elseif log and log.error then
        log.error("字数统计缓存已满，丢弃一条记录")
    end
end

function Writer:flush_buffer()
    if #self.buffer == 0 or not self.handle then return true end

    for i = 1, #self.buffer do
        local ok, err = self.handle:write(self.buffer[i])
        if not ok then
            if log and log.error then
                log.error("CSV 缓存写入失败: " .. tostring(err))
            end
            return false
        end
    end

    self.buffer = {}
    return true
end

function Writer:append(line)
    -- 不再为每次上屏额外 io.open 检查文件，避免高频文件访问和锁竞争。
    if not self.handle and not self:open() then
        self:buffer_line(line)
        return false
    end

    if not self:flush_buffer() then
        self:close()
        self:buffer_line(line)
        return false
    end

    local ok, err = self.handle:write(line)
    if not ok then
        if log and log.error then
            log.error("CSV 写入失败，缓存到内存: " .. tostring(err))
        end
        self:close()
        self:buffer_line(line)
        return false
    end

    -- 保留原有的立即落盘行为；不再额外打开文件检查存在性。
    local flush_ok, flush_err = self.handle:flush()
    if not flush_ok then
        if log and log.error then
            log.error("CSV 刷盘失败: " .. tostring(flush_err))
        end
        self:close()
        return false
    end

    return true
end

-- ============ 主逻辑 ============
local function on_commit(context, writer)
    if not writer then return end

    local text = context:get_commit_text()
    if not text or text == "" then return end

    local n = count_cjk(text)
    if n == 0 then return end

    if writer.plaintext then
        writer:append(string.format("%s,%d,%s\n", get_timestamp(), n, csv_escape(text)))
    else
        writer:append(string.format("%s,%d,\n", get_timestamp(), n))
    end
end

function M.init(env)
    local ok, writer_or_err = pcall(Writer.new, default_csv_path(), ENABLE_PLAINTEXT)
    if not ok then
        if log and log.error then
            log.error("字数统计 Writer 初始化失败: " .. tostring(writer_or_err))
        end
        return
    end

    env.words_counter_writer = writer_or_err

    local connect_ok, connection_or_err = pcall(function()
        return env.engine.context.commit_notifier:connect(function(context)
            local callback_ok, callback_err = pcall(on_commit, context, env.words_counter_writer)
            if not callback_ok and log and log.error then
                log.error("字数统计上屏回调失败: " .. tostring(callback_err))
            end
        end)
    end)

    if connect_ok and connection_or_err then
        env.words_counter_connection = connection_or_err
    else
        env.words_counter_writer:close()
        env.words_counter_writer = nil
        if log and log.error then
            log.error("无法连接 commit_notifier: " .. tostring(connection_or_err))
        end
    end
end

function M.fini(env)
    if env.words_counter_connection then
        pcall(function() env.words_counter_connection:disconnect() end)
        env.words_counter_connection = nil
    end

    if env.words_counter_writer then
        pcall(function() env.words_counter_writer:close() end)
        env.words_counter_writer = nil
    end
end

return M
