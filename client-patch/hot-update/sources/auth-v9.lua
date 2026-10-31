local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local S = require("Common/Singleton")
local U = require("Common/Util")
local authWithBearer = false
local accountSessionUpdatedCallback

function SetupWindow()
  REF.InputName.UIInput.characterLimit = 64
  REF.InputPassword.UIInput.characterLimit = 64
  WU.BindButtonEvent(REF.ButtonCancel, OnCancelClick)
  WU.BindButtonEvent(REF.ButtonConfirm, OnConfirmClick)
  local delegate = CS.EventDelegate
  delegate.Add(REF.InputName.UIInput.onChange, OnInputNameChange)
  delegate.Add(REF.InputPassword.UIInput.onChange, OnInputPasswordChange)
  this:RegisterGameEvent("OnLinkClick", OnLinkClickEvent)
end

function InitWindow()
  local accountAuth = this:GetData("accountAuth")
  REF.InputName.UIInput.defaultText = WU.GetString("WindowLogin_TextAccount")
  REF.InputPassword.UIInput.defaultText = WU.GetString("WindowLogin_TextPassword")
  if accountAuth.entry and accountAuth.entryType ~= "guest" then
    REF.InputName.UIInput.value = accountAuth.entry
    if accountAuth.accessToken and accountAuth.accessToken ~= "" then
      REF.InputPassword.UIInput.value = "******"
      authWithBearer = true
    else
      REF.InputPassword.UIInput.value = accountAuth.password
      authWithBearer = false
    end
  else
    REF.InputName.UIInput.value = ""
    REF.InputPassword.UIInput.value = ""
    authWithBearer = false
  end
end

function OnCancelClick()
  WU.RecycleWindow(this)
end

function OnConfirmClick()
  if authWithBearer then
    WU.RecycleWindow(this)
    this:BroadcastGameEvent("OnAccountAuthSuccess")
  else
    local entry = U.trim(REF.InputName.UIInput.value)
    local password = U.trim(REF.InputPassword.UIInput.value)
    local entryType = GetEntryType(entry)
    if entry == "" then
      WU.ShowHintText(WU.GetString("WindowLogin_AccontEmpty"))
      return
    elseif entryType == "unknow" then
      WU.ShowHintText(WU.GetString("WindowLogin_InvalidUserName"))
      return
    end
    if password == "" then
      WU.ShowHintText(WU.GetString("WindowLogin_PasswordEmpty"))
      return
    end
    RequestAccountAuth(entry, entryType, password)
  end
end

function OnLinkClickEvent(href)
  if href == "event://recall_password" then
    print("Account", href)
    CS.SceneLogin.Instance:OnOpenResetPasswordPage()
  end
end

function OnInputNameChange()
  REF.InputPassword.UIInput.value = ""
  authWithBearer = false
end

function OnInputPasswordChange()
  this:SetData("accountAuth/password", "")
  authWithBearer = false
end

function OnInputOpenIdChange()
  REF.InputLdapPassword.UIInput.value = ""
  authWithBearer = false
end

function OnInputLdapAccountChange()
  REF.InputLdapPassword.UIInput.value = ""
  authWithBearer = false
end

function OnInputLdapPasswordChange()
  this:SetData("accountAuth/password", "")
  authWithBearer = false
end

function UpdateAccountSession(callback)
  accountSessionUpdatedCallback = callback
  local luaTable = S:Get("ConfigDataManager").ClientBasicInfo()
  local wireFormatTable = ProtobufT("ApiAccountSession", luaTable)
  this:AccountRequest("/AccountSession/"):Post(wireFormatTable, OnAccountSessionResponse)
end

function OnAccountSessionResponse(response)
  this:SetData("account/loginAccountToken", response.accountToken)
  accountSessionUpdatedCallback()
end

function RequestAccountAuth(entry_, entryType_, password_)
  UpdateAccountSession(function()
    this:SetData("accountAuth", {
      entry = entry_,
      entryType = entryType_,
      password = password_
    })
    local token = this:GetData("account/loginAccountToken")
    local credentials = {
      entryType = "password",
      username = entry_,
      password = password_
    }
    this:AccountRequest("/Account/"):AddHeader("Authorization", "AccountToken " .. token):Post(credentials, function()
      this:AccountRequest("/Auth/"):AddHeader("Authorization", "AccountToken " .. token):Post(credentials, OnAccountAuthResponse, OnAccountAuthResponseFailed)
    end, OnAccountAuthResponseFailed)
  end)
end

function OnAccountAuthResponse(response)
  print("Account", response.tokenAccess)
  local accountAuth = this:GetData("accountAuth")
  local accountCache = this:GetData("accountCache")
  accountCache.accountId = response.accountId
  accountCache.accountEntry = accountAuth.entry
  accountCache.entryType = accountAuth.entryType
  accountCache.openId = response.openId
  accountCache.accessToken = response.tokenAccess
  accountCache.expireTime = response.createTime + response.expireSeconds
  accountCache.localeId = CS.ConfigDataManager.Instance.CurrentDynamicConfig.Id
  -- Account/MySpace windows call U.ToEnum(Msdk.ePlatform, socialPlatform);
  -- non-MSDK (password/guest) logins have none, so use ePlatform_None (0).
  accountCache.socialPlatform = accountCache.socialPlatform or 0
  accountCache.socialEntry = accountCache.socialEntry or ""
  WU.RecycleWindow(this)
  this:SetData("accountCache", accountCache)
  this:BroadcastGameEvent("OnAccountAuthSuccess")
end

function OnAccountAuthResponseFailed(result)
  local code
  if result ~= nil then
    code = result.error and result.error.code
    if code == "Error_CDKEYNotActivation" then
      WU.RecycleWindow(this)
      this:BroadcastGameEvent("ShowActivationCode")
      return true
    end
    code = code or (result.notify and result.notify.code)
    if code == "Error_CDKEYNotActivation" then
      WU.RecycleWindow(this)
      this:BroadcastGameEvent("ShowActivationCode")
      return true
    end
  end
  if REF.InputPassword then
    REF.InputPassword.UIInput.value = ""
  end
  if code ~= "Error_PasswordDoNotMatch" and code ~= "Error_AccountNotExist" then
    code = "WindowLogin_LoginFailed"
  end
  WU.ShowHintText(WU.GetString(code))
  return true
end

function GetEntryType(entry)
  if string.match(entry, "^[%w_%.@%-]+$") and 3 <= #entry and #entry <= 64 then
    return "password"
  else
    return "unknow"
  end
end
