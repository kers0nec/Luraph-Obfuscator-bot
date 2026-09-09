-- ============================================================================
-- executor_worker.lua
-- Run this INSIDE a Roblox executor (Synapse / Krnl / Fluxus / etc.), i.e. in
-- a LuaU/Roblox environment where obfuscation can actually happen.
--
-- What it does:
--   1. Polls your Render-hosted bot for queued obfuscation jobs.
--   2. For each job it calls obfuscate_source(code, filename)  <- EDIT THIS
--   3. Posts the result back to the bot, which forwards it to Discord.
--
-- The two sides are linked by a shared BRIDGE_KEY (set both in Render's env
-- vars and below).
-- ============================================================================

local RENDER_URL = "https://YOUR-RENDER-SERVICE-NAME.onrender.com"  -- TODO
local BRIDGE_KEY = "12345"                                         -- same as Render's BRIDGE_KEY

local HttpService = game:GetService("HttpService")

local HEADERS = {
    ["Content-Type"] = "application/json",
    ["X-Bridge-Key"] = BRIDGE_KEY,
}

-- ============================================================================
-- !! IMPORTANT !!  obfuscation engine
-- ----------------------------------------------------------------------------
-- The obfuscator.lua committed to this repo is NOT the obfuscation engine.
-- It is Luraph *protected sample output* (it begins "This file was protected
-- using Luraph v15.0").  It can only be run inside Roblox, and even then it is
-- the encoded program, not a tool that obfuscates new code.
--
-- To actually generate obfuscated scripts you must point obfuscate_source at
-- the REAL Luraph / obfuscation engine your executor uses.  How you call it
-- depends on that tool — the one thing to change is this function.  It should
-- take plain source code and return the obfuscated code as a string.
-- ============================================================================
local function obfuscate_source(code, filename)
    -- Example placeholder that mirrors how a real engine might be invoked:
    --   local engine = loadstring(HttpService:GetAsync(RENDER_URL .. "/api/assets/obfuscator.lua", false, HEADERS))()
    --   -- ... or your executor's Luraph tool ...
    --   return engine:obfuscate(code)

    error("obfuscate_source() is not wired to a real obfuscation engine yet. " ..
          "Edit executor_worker.lua and point it at your actual Luraph engine. " ..
          "(The bundled obfuscator.lua is Luraph-protected sample output, not an engine.)")
end

-- ----------------------------------------------------------------------------
-- Bridge protocol helpers
-- ----------------------------------------------------------------------------
local function get_next_job()
    local ok, res = pcall(function()
        return HttpService:GetAsync(RENDER_URL .. "/api/job/next", false, HEADERS)
    end)
    if not ok then
        return nil, res
    end
    return HttpService:JSONDecode(res)
end

local function post_result(job_id, success, outputOrError)
    local body = HttpService:JSONEncode({
        success = success,
        output = success and outputOrError or nil,
        error = success and nil or outputOrError,
    })
    pcall(function()
        HttpService:PostAsync(
            RENDER_URL .. "/api/job/" .. job_id .. "/result",
            body,
            Enum.HttpContentType.ApplicationJson,
            false,
            HEADERS
        )
    end)
end

-- ----------------------------------------------------------------------------
-- Main loop
-- ----------------------------------------------------------------------------
print("[executor_worker] starting. Render URL:", RENDER_URL)

while task.wait(2) do  -- poll every 2s (use wait() on older executors)
    local job, err = get_next_job()
    if err then
        warn("[executor_worker] poll error:", err)
    elseif job then
        print("[executor_worker] obfuscating job", job.job_id, "-", job.filename)
        local ok, result = pcall(obfuscate_source, job.code, job.filename)
        if ok then
            post_result(job.job_id, true, result)
            print("[executor_worker] job", job.job_id, "done")
        else
            post_result(job.job_id, false, tostring(result))
            warn("[executor_worker] job", job.job_id, "failed:", tostring(result))
        end
    end
    -- otherwise no jobs queued, just loop again
end
