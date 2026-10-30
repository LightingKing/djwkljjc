-- Local sandbox purchase adapter (runtime-only).
--
-- This module is intentionally kept outside the protected APK resources.  It
-- can be loaded by the REA/Lua runtime seam after local-guest-entry.lua has
-- selected the localhost account server.  It does not replace native Midas
-- code and it never accepts, creates, or forwards a platform receipt.
--
-- Usage from a runtime entry script:
--
--   local install = require('local-sandbox-iap')
--   install(require('Common/Singleton'):Get('IAPHandler'))
--
-- The installer is a no-op unless the account server is the exact local
-- HTTP endpoint and the account is a guest account.  Other accounts keep the
-- stock Purchase function, so this helper cannot silently redirect a real
-- account to the local sandbox.

local M = {}

local LOCAL_ACCOUNT_URL = '^http://127%.0%.0%.1:19080/?$'
local PURCHASE_PATH = 'fci/purchasetouch/'
local DELIVERY_PATH = 'fci/purchasedelevery/'

local function localServerSelected()
  local restifizer = CS.Restifizer.RestifizerManager.Instance
  if restifizer == nil then
    return false
  end
  local url = restifizer.AccountServerUrl
  return type(url) == 'string' and url:match(LOCAL_ACCOUNT_URL) ~= nil
end

local function localGuest(iap)
  if not localServerSelected() then
    return false
  end
  -- local-guest-entry.lua sets this marker before the asynchronous guest
  -- signup flow starts.  It also survives Login window recycling.
  if iap and iap.__localGuestAttempt == true then
    return true
  end
  local dataBinding = CS.DataBindingManager.Instance
  if dataBinding == nil then
    return false
  end
  local cache = dataBinding:GetData('accountCache')
  return cache ~= nil and cache.entryType == 'guest'
end

local function dataField(data, ...)
  if data == nil then
    return nil
  end
  local value = data
  if type(data) == 'string' and type(JsonD) == 'function' then
    local ok, decoded = pcall(JsonD, data)
    if ok then
      value = decoded
    end
  end
  for i = 1, select('#', ...) do
    local key = select(i, ...)
    local candidate = value[key]
    if candidate ~= nil then
      return candidate
    end
  end
  return nil
end

local function asText(value)
  if value == nil then
    return nil
  end
  local text = tostring(value)
  if text == '' then
    return nil
  end
  return text
end

local function showFailure(WU, text)
  local message = text or '本地沙盒购买失败，请稍后重试'
  if WU and type(WU.ShowHintText) == 'function' then
    WU.ShowHintText(message)
  end
end

local function unlock(WU)
  if WU and type(WU.SetLockFlag) == 'function' and WU.LockInputFlag ~= nil then
    WU.SetLockFlag(WU.LockInputFlag.Purchase, false)
  end
end

local function finish(iap, WU)
  unlock(WU)
  local dataBinding = CS.DataBindingManager.Instance
  if dataBinding ~= nil then
    -- The server has already committed the resource ledger.  Refresh the
    -- bound base-resource object so the shop UI sees the new balance.  Both
    -- calls are guarded because older clients may not expose SyncRemote.
    pcall(function()
      dataBinding:SyncRemote(dataBinding:GameRequest('fci/baseresource/'):SyncInput(false))
    end)
    pcall(function()
      dataBinding:BroadcastGameEvent('FinishPurchase')
    end)
  end
  -- Do not call IAPHandler.FinishPurchase here.  The stock function invokes
  -- Midas GetActivityInfo(), which is intentionally unavailable for a local
  -- guest account.  Broadcasting the same UI event and refreshing resources
  -- is the safe local equivalent.
end

local function installPurchase(iap, WU)
  local original = iap.Purchase
  if type(original) ~= 'function' or iap.__localSandboxPurchase == true then
    return false
  end

  iap.Purchase = function(...)
    local args = {...}
    -- Support both dot and colon calls without changing the stock signature.
    local offset = args[1] == iap and 1 or 0
    local product = args[offset + 1]
    local channel = args[offset + 2]
    local count = args[offset + 3]
    if not localGuest(iap) then
      if offset == 1 then
        return original(iap, product, channel, count)
      end
      return original(product, channel, count)
    end
    if type(product) ~= 'table' and type(product) ~= 'userdata' then
      showFailure(WU, '本地沙盒商品数据无效')
      return
    end
    count = tonumber(count) or 1
    if count < 1 then
      showFailure(WU, '本地沙盒购买数量无效')
      return
    end

    local productId = asText(dataField(product, 'productId', 'product_id'))
    if productId == nil then
      showFailure(WU, '本地沙盒商品编号无效')
      return
    end
    local price = dataField(product, 'price')
    local message = string.format(
      '本地沙盒购买\n商品：%s\n数量：%d\n价格：%s\n不会连接真实支付或发送平台回执。',
      productId, count, price ~= nil and tostring(price) or '本地测试'
    )

    local function requestDelivery(tradeData)
      if tradeData == nil then
        unlock(WU)
        showFailure(WU, '沙盒下单没有返回交易令牌')
        return
      end
      -- Only the server-issued local-sandbox token is sent.  No receipt,
      -- openid, access token, or platform SDK value is accepted here.
      local dataBinding = CS.DataBindingManager.Instance
      local delivery = {
        tradeData = tradeData,
        -- PurchasePlatform_none (0) is the protobuf enum value reserved for
        -- local/offline adapters.  A string such as "local_sandbox" would
        -- fail native wireformat serialization before reaching the server.
        platform = 0,
        channel = tonumber(channel) or 0
      }
      local request = dataBinding:GameRequest(DELIVERY_PATH)
      request:Post(delivery, function(response)
        local transactionId = dataField(response, 'transactionId', 'transaction_id')
        if transactionId == nil then
          showFailure(WU, '沙盒发货响应无效')
          unlock(WU)
          return
        end
        finish(iap, WU)
      end, function()
        unlock(WU)
        showFailure(WU)
      end)
    end

    if WU and type(WU.SetLockFlag) == 'function' and WU.LockInputFlag ~= nil then
      WU.SetLockFlag(WU.LockInputFlag.Purchase, true)
    end
    local dataBinding = CS.DataBindingManager.Instance
    local request = dataBinding:GameRequest(PURCHASE_PATH)
    request:Post({
      isGuest = true,
      productId = productId,
      platform = 0,
      channel = tonumber(channel) or 0,
      count = count
    }, function(response)
      local tradeData = dataField(response, 'tradeData', 'trade_data')
      if tradeData == nil then
        unlock(WU)
        showFailure(WU, '沙盒下单响应无效')
        return
      end
      requestDelivery(asText(tradeData))
    end, function()
      unlock(WU)
      showFailure(WU)
    end)
  end
  iap.__localSandboxPurchase = true
  return true
end

function M.install(iap)
  if iap == nil then
    return false
  end
  local WU = require('Common/WindowUtil')()
  return installPurchase(iap, WU)
end

-- Keep ``require('local-sandbox-iap')`` convenient while exposing a named
-- method for callers that prefer an explicit module table.
setmetatable(M, {__call = function(_, iap) return M.install(iap) end})

return M
