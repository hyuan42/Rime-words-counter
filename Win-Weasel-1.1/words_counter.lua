--[[

版本: 字数统计工具 v1.1
作者: hyuan
Github仓库: https://github.com/hyuan42/Rime-words-counter
时间: 2026-06-26

脚本功能：将输入法上屏的汉字个数和时间戳追加到本地 CSV。

开启明文模式，把 ENABLE_PLAINTEXT 改成 true。

--]]

local CUSTOM_CSV_PATH  = nil    -- 自定义路径，nil = 自动
local ENABLE_PLAINTEXT = false  -- true = 第三列记录上屏原文

local M = {}

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

local function default_csv_path()
    if CUSTOM_CSV_PATH and CUSTOM_CSV_PATH ~= "" then
        return CUSTOM_CSV_PATH
    end
    if detect_platform() == "windows" then
        local appdata = os.getenv("APPDATA") or "C:\\"
        return appdata .. "\\Rime\\py_wordscounter\\words_input.csv"
    else
        local home = os.getenv("HOME") or ""
        return home .. "/Library/Rime/py_wordscounter/words_input.csv"
    end
end

-- ============ 目录创建：确保 CSV 父目录存在 ============
local function ensure_dir(filepath)
    local dir = filepath:match("^(.*)[/\\][^/\\]*$")
    if not dir or dir == "" then return end
    if detect_platform() == "windows" then
        os.execute('mkdir "' .. dir .. '" 2>nul')
    else
        os.execute('mkdir -p "' .. dir .. '"')
    end
end

-- ============ CSV 字段转义：将文本中的双引号替换，用双引号包裹包含逗号/换行的字段 ============
local function csv_escape(s)
    if s:find('[,"\n\r]') then
        return '"' .. s:gsub('"', '""') .. '"'
    end
    return s
end

-- ============ 写入器：常驻句柄 + 失败缓冲 ============
local Writer = {}
Writer.__index = Writer

function Writer.new(path, plaintext)
    local self = setmetatable({}, Writer)
    self.path = path
    self.plaintext = plaintext
    self.handle = nil
    self.buffer = {}
    self:ensure_header()
    self:open()
    return self
end

function Writer:ensure_header()
    local f = io.open(self.path, "r")
    if f then
        f:close()
        return
    end
    ensure_dir(self.path)  -- 目录不存在时自动创建
    f = io.open(self.path, "w")
    if f then
        f:write("timestamp,chinese_count,text\n")
        f:close()
    end
end

function Writer:open()
    if self.handle then return true end
    local f, err = io.open(self.path, "a")
    if not f then
        if log and log.error then log.error("无法打开 CSV: " .. tostring(err)) end
        return false
    end
    f:setvbuf("line")
    self.handle = f
    return true
end

function Writer:close()
    if self.handle then
        self.handle:close()
        self.handle = nil
    end
end

function Writer:flush_buffer()
    if #self.buffer == 0 or not self.handle then return end
    for i = 1, #self.buffer do
        local ok = pcall(function() self.handle:write(self.buffer[i]) end)
        if not ok then return end
    end
    self.buffer = {}
end


function Writer:append(line)
    -- 若文件被删除，关闭旧句柄并重新创建（含表头）
    local check = io.open(self.path, "r")
    if check then
        check:close()
    else
        self:close()
        self:ensure_header()
    end
    if not self.handle and not self:open() then
        self.buffer[#self.buffer + 1] = line
        return
    end
    self:flush_buffer()
    local ok, err = pcall(function()
        self.handle:write(line)
        self.handle:flush()  -- 强制刷盘，避免缓冲区延迟
    end)
    if not ok then
        if log and log.error then log.error("CSV 写入失败，缓存到内存: " .. tostring(err)) end
        self:close()
        self.buffer[#self.buffer + 1] = line
    end
end

-- ============ 主逻辑 ============
local writer

local function on_commit(context)
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
    writer = Writer.new(default_csv_path(), ENABLE_PLAINTEXT)
    env.engine.context.commit_notifier:connect(on_commit)
end

function M.fini(env)
    if writer then writer:close() end
end

return M
