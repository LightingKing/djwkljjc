local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local SDU = require("Common/SoundUtil")
local m_initScale = 0.5
local m_scaleValue = 0.6
local m_maxScaleOffset = 0.2
local m_scaleUnit = 0.06
local m_thresholdUp, m_thresholdDown
local m_originDistance = 0
local m_lastDistance = 0
local m_dragDelta
local m_twoFingerd = false
local m_nowAudioSource, m_initPos

function SetupWindow()
  WU.BindDragEvent(REF.TextureActor, OnDraging)
  WU.BindPressEvent(REF.TextureActor, OnPress)
  WU.BindPressEvent(REF.BGDrag, OnPress)
  m_initPos = REF.TextureActor.transform.localPosition
end

function InitWindow()
  CS.UnityEngine.Input.multiTouchEnabled = true
  REF.TextureActor.transform.localPosition = m_initPos
end

function UninitWindow()
  CS.UnityEngine.Input.multiTouchEnabled = false
  if m_nowAudioSource ~= nil then
    m_nowAudioSource:Stop()
  end
end

function OnDraging(go, delta)
  local position = REF.TextureActor.transform.localPosition
  position.x = position.x + delta.x
  position.y = position.y + delta.y
  m_dragDelta = m_dragDelta + delta
  if GetDistance(position, m_initPos) <= 500 then
    REF.TextureActor.transform.localPosition = position
  end
end

function OnPress(go, press)
  if press then
    m_dragDelta = CS.UnityEngine.Vector2.zero
  else
    if math.abs(m_dragDelta.x) <= 30 and 30 >= math.abs(m_dragDelta.y) and not m_twoFingerd then
      WU.RecycleWindow(this)
    end
    m_twoFingerd = false
  end
end

function SetTextureScale()
  local scale = m_initScale * (m_scaleValue + m_maxScaleOffset) + (1 - m_scaleValue) / 2
  REF.TextureActor.transform.localScale = CS.UnityEngine.Vector3(scale, scale, scale)
end

function SetActor(actorConfig)
  m_twoFingerd = false
  local config = PB.get("ActorPortrait", actorConfig.id, 0)
  REF.TextureActor.UITexture.mainTexturePath = "Texture/ActorPortraitFull/actor_portrait_full_" .. actorConfig.animRes
  WU.ApplyLocalMissingActorTexture(REF.TextureActor.UITexture, "Texture/ActorPortraitFull/actor_portrait_full_" .. actorConfig.animRes, this)
  REF.BG.UITexture.mainTexturePath = "Texture/ActorPortraitFullBG/" .. config.bg
  m_thresholdUp = m_scaleValue + (1 - m_scaleValue) / 2 + m_maxScaleOffset
  m_thresholdDown = (1 - m_scaleValue) / 2
  REF.TextureActor.transform.localPosition = CS.UnityEngine.Vector3(0, 0, 0)
  m_initScale = 0.5
  REF.Slider.UISlider.value = m_initScale
  SetTextureScale()
  m_nowAudioSource = SDU.PlayActorWiki(actorConfig.role)
end

function GetDistance(vector3One, Vector3Two)
  local diffX = vector3One.x - Vector3Two.x
  local diffY = vector3One.y - Vector3Two.y
  return math.sqrt(diffX * diffX + diffY * diffY)
end

function UpdateWindow(delta)
  if CS.UnityEngine.Input.touchCount >= 2 then
    m_twoFingerd = true
    local touch1 = CS.UnityEngine.Input.GetTouch(0)
    local touch2 = CS.UnityEngine.Input.GetTouch(1)
    if touch1.phase == CS.UnityEngine.TouchPhase.Began or touch2.phase == CS.UnityEngine.TouchPhase.Began then
      m_originDistance = GetDistance(touch1.position, touch2.position)
      m_lastDistance = m_originDistance
    end
    if touch1.phase == CS.UnityEngine.TouchPhase.Moved or touch2.phase == CS.UnityEngine.TouchPhase.Moved then
      local distance = GetDistance(touch1.position, touch2.position)
      local sign = 1
      if distance < m_lastDistance then
        sign = -1
      elseif distance > m_lastDistance then
        sign = 1
      end
      m_lastDistance = distance
      local percent = math.abs(m_originDistance - distance) / m_originDistance
      local scale = sign * m_scaleUnit * percent
      m_initScale = m_initScale + scale
      if m_initScale > m_thresholdUp then
        m_initScale = m_thresholdUp
      elseif m_initScale < m_thresholdDown then
        m_initScale = m_thresholdDown
      end
      SetTextureScale()
    end
  end
end
