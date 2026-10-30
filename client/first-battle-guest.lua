-- Runtime-only first-battle entry. The installed APK is unchanged.
-- Keep the original Login Lua guards: no license acceptance is automated.
local manager=CS.Restifizer.RestifizerManager.Instance
assert(manager and manager.AccountServerUrl and manager.AccountServerUrl:match('^http://127%.0%.0%.1:19080/?$'), 'Local account server must be selected')

local window=CS.UIWindowManager.Instance:FindWindow('Login')
if window==nil then
  return 'FirstBattle guest login already complete'
end
local injector=window:GetComponent(typeof(CS.LuaInjector))
if injector==nil or injector.Sandbox==nil then
  return 'FirstBattle waiting Login Lua environment'
end
local env=injector.Sandbox
if not env.canAuthLogin() then
  return 'FirstBattle waiting canAuthLogin'
end

local preferences=require('Common/Singleton'):Get('ConfigDataManager').GetPreferences()
if not preferences.isAcceptLicense then
  return 'FirstBattle requires accepted license'
end

-- PushService invokes IAPHandler.SyncMidas after auth. The stable local
-- marker is read by client/local-guest-entry.lua's billing guard and avoids
-- any Tencent billing/account call for this local guest flow.
local iap=require('Common/Singleton'):Get('IAPHandler')
iap.__localGuestAttempt=true
-- Let the server return the current device-bound entry.  Reusing a stale
-- accountCache entry can make the fresh AccountSession reject Auth with
-- ``guest entry is not registered`` after a relaunch or clean install.
env.GuestSignup()
return 'FirstBattle local guest auth requested'
