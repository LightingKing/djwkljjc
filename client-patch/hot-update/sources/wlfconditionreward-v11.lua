local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local U = require("Common/Util")
local DBH = require("Manager/DataBindingHandler")
local IU = require("Common/ItemUtil")
local RU = require("Common/RedMarkUtil")
local m_data, m_index

function Start()
  -- The shipped prefab has no texture assigned, although its original art
  -- is present in the bundle set (the asset name intentionally says ture).
  REF.ConditionRewardTexture.UITexture.mainTexturePath = "Texture/Welfare/ture_love"
  WU.BindButtonEvent(REF.ButtonConditionReward, function()
    local reward = this:GetData("ConditionReward")
    if not reward then
      this:GameRequest("fci/conditionreward"):Get(function(res)
        this:SetData("shouldActiveCondition", res.hasRewardQualification and not res.isRewardGot)
        if res.hasRewardQualification and not res.isRewardGot then
          this:SetData("ConditionReward", res.manualBoxReward)
          OpenReward(res.manualBoxReward)
        end
      end)
      return
    end
    OpenReward(reward)
  end)
end

function OpenReward(reward)
  if reward and reward.id then
    WU.AcquireWindowAsync("ManualBoxActor", function(ui)
      _ENV["$"](ui)["$$SetBoxIdTrueLove"](reward.id, function(flag)
        this:SetData("shouldActiveCondition", not flag)
        if flag then
          WU.RecycleWindow("Welfare")
          WU.AcquireWindowAsync("City")
        end
      end)
    end)
  end
end
