
D
account91.protooptions.protogoogle/protobuf/timestamp.proto"9
ApiTest
result
test (	:ÒótestViewÊó/test/"3

AppKeyInfo
app_key (	
app_key_type (	"M
	DbAppInfo
app_id (	
open_id_start (
keys (2.AppKeyInfo"v
DbAccountSwitch
white_id_switch (
cdkey_switch (
booking_full_switch (
mizi_pay_switch ("Í
ApiAccountSwitch
white_id_switch (
cdkey_switch (
booking_full_switch (
mizi_pay_switch (*
result 
result (2.DbAccountSwitch:(ÒóAccountSwitchViewÊó/accountswitch/"f
ApiManageAuthInfo%
get
get (2.DbAccountSession:*ÒóManageAuthInfoViewÊó/ManageAuthInfo/"
DbManageGlobal
apps (	"Ê
ApiManageSession
ldap_account (	
ldap_password (	
client_type (	+
result!
result (2.DbAccountSession:GÒóManageSessionViewÊó/ManageSession/Úóè¿™é‡Œå¯ä»¥åŠ ä¸­æ–‡è¯´æ˜"V

ApiAppById
get
get (2
.DbAppInfo:(ÒóAppViewByIdÊó/App/<string:app_id>/"°
ApiApp
app_id (	
app_key (	
app_key_type (	
get
apps (	 
post
post (2
.DbAppInfo"
patch
patch (2
.DbAppInfo:ÒóAppViewÊó/App/"¶
DbAccountSession
account_token (	
ldap_account (	
	client_ip (	
create_time (
expire_seconds (
app_id (	
app_key_type (	
ad_track_id (	

entry_type (	
entry (	
social_type (	
password_hashed	 (	
	forbidden
 (
captcha (	
captcha_passed (
need_login_captcha3 (
verify_code (	
verify_code_create_time (
verify_code_passed (
password_verify_code (	(
 password_verify_code_create_time  (#
password_verify_code_passed! (
id_china) (	
name* (	
bbrr_uin2 (	
social_platform4 (
channel5 (
	device_id6 (	
device_type7 (
device8 (	"ê
ApiAccountSession
app_id (	
app_key (	

entry_type (	
channel (
	device_id (	
device_type (
device (	+
result!
result (2.DbAccountSession:*ÒóAccountSessionViewÊó/AccountSession/"1
AccountAppInfo
app_id (	
open_id (	"–
	DbAccount

account_id (
password_hashed (	
create_time (
id_china (	
name (	

gm (
entrys (	"
	app_infos (2.AccountAppInfo
	more_info	 (	
	forbidden
 (
forbid_end_time (	
social_type (	
device_type ("6
DbAccountOpenId
open_id (	

account_id ("È
DbAccountEntry
entry (	

entry_type (	

account_id (
verified (
	forbidden (
social_type (	
social_platform (
forbid_end_time (	
device_type	 ("Š
ApiVerifyCode
verify_code (	
verify_code_passed ($
result
verify_code_passed (:"ÒóVerifyCodeViewÊó/VerifyCode/"à
ApiPasswordVerifyCode
entry (	

entry_type (	
password_verify_code (	#
password_verify_code_passed (-
result#
password_verify_code_passed (:2ÒóPasswordVerifyCodeViewÊó/PasswordVerifyCode/"ù
ApiCurrentEntryModifyVerifyCode
entry (	

entry_type (	
social_tokey (	
verify_code (	
verify_code_passed ($
result
verify_code_passed (:FÒó EntryModifyVerifyCodeCurrentViewÊó/CurrentEntryModifyVerifyCode/"é
ApiNewEntryModifyVerifyCode
verify_code (	
verify_code_passed (?
result
verify_code_passed (
auth (2.DbAuthInfo:XÒóEntryModifyVerifyCodeNewViewÊó4/NewEntryModifyVerifyCode/,/exist-entry-verify-code/"Í
ApiNewEntry
entry (	

entry_type (	
social_type (	
social_token (	
password (	>
post
entry (	

entry_type (	
social_type (	:ÒóNewEntryViewÊó
/NewEntry/"œ
ApiExistEntry
entry (	

entry_type (	
social_type (	
social_token (	
bbrr_uin (	
post:#ÒóExistEntryViewÊó/exist-entry/"à
ApiEntryReg
entry (	

entry_type (	
password (	
social_type (	
ad_track_id (	
bbrr_uin
 (	@
result
entry (	

entry_type (	
social_type (	:ÒóEntryRegViewÊó
/EntryReg/"†

ApiCaptcha
captcha (	
captcha_passed (1
result
captcha (	
captcha_passed (:ÒóCaptchaViewÊó	/Captcha/"z
ApiExtraInfo
id_china (	
name (	(
result
id_china (	
name (	: ÒóExtraInfoViewÊó/ExtraInfo/"Œ
ApiExtraInfoByAuth
id_china (	
name (	(
result
id_china (	
name (	:,ÒóExtraInfoByAuthViewÊó/ExtraInfoByAuth/"?
ApiPassword
password (	:ÒóPasswordViewÊó
/Password/"]
ApiGmPassword

account_id (
password (	:&ÒóApiGmPasswordViewÊó/gm-password/"ƒ
ApiPasswordReset

account_id (
result
new_password (	:;ÒóPasswordResetViewÊó/Password/reset/Úógmé‡ç½®å¯†ç "•

ApiAccount

gm (
	forbidden (
forbid_end_time	 (	C
post
entry (2.DbAccountEntry
account (2
.DbAccountC
get
account (2
.DbAccount
entrys (2.DbAccountEntry:EÒóAccountViewÊó2/Account/,/Account/<string:account_id_or_open_id>/"÷
ApiAccountEntry
entry (	

entry_type (	

account_id (
verified (
	forbidden (
forbid_end_time (	)
result
result (2.DbAccountEntry:DÒóAccountEntryViewÊó,/AccountEntry/,/AccountEntry/<string:entry>/"Ú
ApiGmAccountEntry
entry (	

entry_type (	

account_id (
verified (
	forbidden ()
result
result (2.DbAccountEntry:>ÒóApiGmAccountEntryViewÊó!/gm-account-entry/,/gm-add-entry/"u
ApiAccountEntryExist
result
result (:CÒóApiAccountEntryExistViewÊó#/AccountEntry/<string:entry>/exist/"f
ApiAccountGuestBind 
post
post (2
.DbAccount:-ÒóApiAccountBindViewÊó/AccountEntry/bind/"x
ApiAccountBbrrOldBind 
post
post (2
.DbAccount:=ÒóApiAccountBindBbrrOldViewÊó/AccountEntry/bind-bbrr-old/"

DbAuthInfo
token_access (	
app_id (	
app_key_type (	
create_time (
expire_seconds (
open_id (	

account_id (
entry (	

entry_type	 (	
verified
 (
need_real_name (1
entry_change (2.DbAuthInfo.EntryChangeInfo
social_nick_name (	
need_pop (
realname_flag (®
EntryChangeInfo
verify_code (	
verify_code_passed (
entry_to_change (	
entry (	

entry_type (	
social_type (	
password_hashed (	
new_verify_code (	
new_verify_code_passed (
captcha (	
captcha_passed (
id_china (	
name  (	"
ApiAuthInfo
entry (	

entry_type (	
social_type (	
password (	
social_token (	
social_platform (
social_expire (
device_type (
cdkey	 (	%
result
result (2.DbAuthInfo:3ÒóAuthViewÊó#/Auth/,/Auth/<string:verify_token>/"À
ApiAuthByLdap
open_id (	
ldap_account (	
ldap_password (	%
result
result (2.DbAuthInfo:JÒóApiAuthByLdapViewÊó/Auth-ldap/Úó"é€šè¿‡ldapè®¤è¯ç™»é™†ä»»æ„ç©å®¶"Ç
ApiBbrrImport
uid (
uin (	
src (	V
post
new_user_id (	
new_password (	
open_id (	

account_id (:7ÒóBbrrImportViewÊó/bbrr-import/Úóå¯¼å…¥bbrrè´¦å·"o
ApiRealNameVerified
get
get (:DÒóApiRealNameVerifiedViewÊó%/real-name-verified/<string:open_id>/"t
ApiCheckMailEntryExist
get
get (:FÒóApiCheckMailEntryExistViewÊó$/entry-exist/email/<string:open_id>/"v
ApiCheckPhoneEntryExist
get
get (:GÒóApiCheckPhoneEntryExistViewÊó$/entry-exist/phone/<string:open_id>/"i
ApiCheckOpenIdExist
get
get (:>ÒóApiCheckOpenIdExistViewÊó/openid-exist/<string:open_id>/"Ó
ApiClickGame
device (	
channel (
client_version (	
device_type (
	device_id (	
result
result (:HÒóClickGameViewÊó/ClickGame/Úó$è®°å½•ç©å®¶ç‚¹å‡»æ¸¸æˆè¡Œä¸ºè¯·æ±‚"º
ApiWhiteOpenId
open_ids (	?
result
result (
white_count (
open_ids (	:UÒóWhiteOpenIdViewÊó+/WhiteOpenId/,/WhiteOpenId/<string:openid>/Úóopenidç™½åå•"œ
ApiUploadWhiteOpenId?
result
result (
white_count (
open_ids (	:CÒóUploadWhiteOpenIdViewÊó/UploadWhiteOpenId/Úóopenidç™½åå•"±
ApiDelWhiteOpenId
open_ids (	?
result
result (
white_count (
open_ids (	:IÒóDelWhiteOpenIdViewÊó/DelWhiteOpenId/Úóåˆ é™¤æŒ‡å®šopenidç™½åå•"–
ApiDelAllWhiteOpenId-
result
result (
white_count (:OÒóDelAllWhiteOpenIdViewÊó/DelAllWhiteOpenId/Úóå…¨éƒ¨åˆ é™¤openidç™½åå•"C
DbTodayRegister
last_register_time (
register_num (bproto3
‰
achievement.protooptions.protocommon.protoxlsx_server.proto"Â
DbPlayerAchievement
	player_id (	<
achievements (2&.DbPlayerAchievement.AchievementsEntry
AchievementInfo
progress (

reward_got (
is_done (
	done_time (

extra_time (
unique (Y
AchievementsEntry
key (3
value (2$.DbPlayerAchievement.AchievementInfo:8"€
ApiAchievementSummary
get

can_reward (:LÒóApiAchievementSummaryViewÊó/achievement-summary/Úóä»»åŠ¡æ‘˜è¦ä¿¡æ¯"Œ
ApiAchievement

reward_got (
progress (^
result

id (
progress (
is_done (

reward_got (
	done_time (:tÒóApiAchievementViewÊó0/achievement/,/achievement/<int:achievement_id>/Úó&æˆå°±æŸ¥è¯¢,ä»»åŠ¡ä¿®æ”¹,ä»»åŠ¡åˆ·æ–°"Û
ApiAchievementRewardb
result&

res_change (2.STResourceDynamic0
new_achievements (2.ApiAchievement.result:_ÒóApiAchievementRewardViewÊó)/achievement/<int:achievement_id>/reward/Úóæˆå°±å¥–åŠ±é¢†å–"ª
ApiGmSetAchievement
res_id (
add (
result:lÒóApiGmSetAchievementViewÊó /achievement/gm-set-achievement/Úó)GMè®¾ç½®æˆå°±è¿›åº¦æˆ–å®Œæˆæ‰€æœ‰æˆå°±"Ù
ApiAchievementClientDone
achievement_id (
add (
result:ÒóApiAchievementClientDoneViewÊó/achievement/client-done/ÚóLä¸€äº›å®¢æˆ·ç«¯å¯ä»¥è‡ªå·±æ ‡è®°å®Œæˆçš„æˆå°±ç”¨è¿™ä¸ªæ¥å£Postæ¥å®Œæˆbproto3
ã
activities.protooptions.protoxlsx_server.protocommon.proto"g
ApiActivity%
result
result (2.Activities:1ÒóApiActivityViewÊó
/activity/Úóæ´»åŠ¨åˆ—è¡¨"‰
ApiActivityPreOpen%
result
result (2.Activities:LÒóApiActivityPreOpenViewÊó/activity-preopen/Úóå³å°†å¼€æ”¾æ´»åŠ¨åˆ—è¡¨"}
ApiActivityOpen%
result
result (2.Activities:CÒóApiActivityOpenViewÊó/activity-open/Úóå¼€æ”¾çš„æ´»åŠ¨åˆ—è¡¨"¾
ApiActivityPurblePairsCreate*
result
data (

can_reward (:rÒó ApiActivityPurblePairsCreateViewÊó/activity-purblepairs/create/Úó)post-è·å¾—ç¿»ç‰Œå°æ¸¸æˆçš„åˆå§‹æ•°æ®"á
ApiActivityPurblePairsBalance
match_pairs (

index_list (0
result&

res_change (2.STResourceDynamic:eÒó!ApiActivityPurblePairsBalanceViewÊó/activity-purblepairs/balance/Úópost-ç¿»ç‰Œå°æ¸¸æˆç»“ç®—bproto3
»
actor_cultivation.protooptions.protocommon.proto"ï
ActorCultivationInfo
activity_id (
actor_id (
progress (
progress_daily (
progress_reward (
progress_reward_vip
 (
vip (

start_time (
end_time (
new	 (
show_end ("B
ActorCultivationInfoMessage#
info (2.ActorCultivationInfo"ô
ApiActorCultivation
progress (
actor_id (/
result%
result (2.ActorCultivationInfo:‡ÒóApiActorCultivationViewÊó9/actor-cultivation/,/actor-cultivation/<int:activity_id>/Úó+å¼€æ”¾çš„è§’è‰²å…»æˆè®¡åˆ’,gm patch è¿›åº¦"Ç
ApiActorCultivationNewFlag/
result%
result (2.ActorCultivationInfo:xÒóApiActorCultivationNewFlagViewÊó)/actor-cultivation/<int:activity_id>/new/Úó%å¼€æ”¾çš„è§’è‰²å…»æˆè®¡åˆ’,newæ ‡è®°"Ğ
ApiActorCultivationShowEnd
actor_id (/
result%
result (2.ActorCultivationInfo:oÒóApiActorCultivationShowEndViewÊó/actor-cultivation/showend/Úó*å¼€æ”¾çš„è§’è‰²å…»æˆè®¡åˆ’,show-endæ ‡è®°"¾
ApiActorCultivationDone/
result%
result (2.ActorCultivationInfo:rÒóApiActorCultivationDoneViewÊó*/actor-cultivation/<int:activity_id>/done/Úó!è§’è‰²å…»æˆè®¡åˆ’å®¢æˆ·ç«¯å®Œæˆ"÷
ApiActorCultivationUpgradeVipU
result#
info (2.ActorCultivationInfo&

res_change (2.STResourceDynamic:Òó!ApiActorCultivationUpgradeVipViewÊó1/actor-cultivation/<int:activity_id>/upgrade-vip/Úó!è§’è‰²å…»æˆè®¡åˆ’ä»»åŠ¡å‡çº§vip"Ğ
ApiActorCultivationTaskReward/
result%
result (2.ActorCultivationInfo:~Òó!ApiActorCultivationTaskRewardViewÊó-/task/<int:task_id>/reward-actor-cultivation/Úó$è§’è‰²å…»æˆè®¡åˆ’ä»»åŠ¡å¥–åŠ±é¢†å–"¢
!ApiActorCultivationProgressReward
activity_id (
progress (
vip (U
result#
info (2.ActorCultivationInfo&

res_change (2.STResourceDynamic:rÒó%ApiActorCultivationProgressRewardViewÊó#/actor-cultivation/progress-reward/Úóè§’è‰²å…»æˆè®¡åˆ’è¿›åº¦å¥–åŠ±bproto3
•N
actor_logic_data_proto.proto"$
Vector2Proto	
x (	
y ("/
Vector3Proto	
x (	
y (	
z ("8

ColorProto	
r (	
g (	
b (	
a ("<
	TickRange
start (
end (
hitInterval ("x
LogicBoxProto
a (2.Vector2Proto
b (2.Vector2Proto
c (2.Vector2Proto
range (2
.TickRange"C

FreezeData
start (
duration (
freezeMotion ("|
ChangePosData

faceOption (
flash (
	posOption (
position (2.Vector3Proto
randomTarget ("±

VictimData
action (	
speed (2.Vector3Proto
freeze (2.FreezeData!
	changePos (2.ChangePosData
binding ($
bindingOffset (2.Vector2Proto"S
	AudioData
name (	
volume (
start (
tag (	
path (	"N
	ComboData
key (
action (	

faceOption (
afterHit ("3
InheritSpeedData	
x (	
y (	
z ("Æ
EffectObjectData
name (	
start (
rotation (
scale (2.Vector2Proto

behindRole (
binding (
durationBinding (
position (2.Vector2Proto"
offPosition	 (2.Vector2Proto
customDurationId
 (
absolutePos (

randomPlay (
hideEffectWhenNoVisible (":
RemoveCustomDurationEffectData

id (
tick ("À
AddRoleData
characterID (
start (
endTime (
startActions
 (	
speed (2.Vector3Proto
scale (
rotation ( 
position (2.ChangePosData
endTimeAction (	

deadAction	 (	

faceOption (
independence (
code (
	drawOrder (
palette ("ƒ
ControlRoleData
type (
code (
start (
action (	 
position (2.ChangePosData
changeSkill ("ƒ
CameraShakeData
start (!

shakeRange (2.Vector2Proto
shakeK (2.Vector2Proto
count (
interval (">
SceneFadeData
start (
duration (
type ("=
CameraFovData
start (
fov (
duration ("-
TimeScaleData
start (
scale ("l
ActionHitData
hitBox (2.LogicBoxProto
	hitFreeze (2.FreezeData
victim (2.VictimData"¯
ActionLogicDataProto
name (	
animate (	#

actionType (2.ActionTypeEnum
actorIdForResourceJ (
inputKey (

inputKeyEx (
coldDown9 (

superArmor7 (
damage8 (
cost< (
flyin (2.FlyinTypeEnum)
collisionType	 (2.CollisionTypeEnum

speedStart4 (
speed (2.Vector3Proto#
acceleration (2.Vector3Proto'
inheritSpeed (2.InheritSpeedData 
startPos$ (2.ChangePosData!
	finishPos% (2.ChangePosData 
behitBox (2.LogicBoxProto
behitBoxIgnoreDamage (#
hitDataListP (2.ActionHitData
fciRoleLayerQ (
hitBox (2.LogicBoxProto
hitBoxIgnoreDamage
 (
	hitFreeze (2.FreezeData
victim (2.VictimData
pushFreezeTimeG (%

hitEffects' (2.EffectObjectData%

addEffects( (2.EffectObjectData5
removeEffect) (2.RemoveCustomDurationEffectData(
hitCameraShake, (2.CameraShakeData+
onTimeCameraShake- (2.CameraShakeData
selfVictimAction (	
selfVictimFace (
addRoles* (2.AddRoleData&
controlRoles+ (2.ControlRoleData&
sceneFadeStart5 (2.SceneFadeData$
sceneFadeEnd6 (2.SceneFadeData&
actorFadeStartS (2.SceneFadeData$
actorFadeEndT (2.SceneFadeData
protectTime (

comboRange (2
.TickRange
	interrupt (	
	hitAction (	

nextAction (	
nextActionEx (	
combo (2
.ComboData
comboEx (2
.ComboData

dropAction (	
dropEndAction (	
deadTime  (

deadAction! (	
hitAudio (2
.AudioData
audio# (2
.AudioData
overDriveDuration3 (
antiBinding& (
alphaOption. (
rotateSpeed/ (
rotateCenterX0 (
rotateCenterY1 (
useRotateCenter2 (
rotateAbsValO (
addAttackMark: (
removeAttackMark; (
totalDuration (
realName (	#

attackType" (2.AttackTypeEnum
distanceF ("

camFovDataI (2.CameraFovData
colorMaskTimeBeginK ((
colorMaskValueBeginL (2.ColorProto
colorMaskTimeEndM (&
colorMaskValueEndN (2.ColorProto
breakConjureU (

breakAudioV (2
.AudioData'
breakEffectsW (2.EffectObjectData&
timeScaleParamX (2.TimeScaleData"L
ActorLogicDataProto
aniXml (	%
action (2.ActionLogicDataProto*
ActionTypeEnum
ActionType_None 
ActionType_Attack
ActionType_Standby
ActionType_Walk
ActionType_Run
ActionType_NoLearn
ActionType_Ultimate
ActionType_Passive
ActionType_Dead
ActionType_Drop	
ActionType_WinPose

ActionType_Pose*S
FlyinTypeEnum
FlyinType_None 
FlyinType_NoDropEnd
FlyinType_DropEnd*C
CollisionTypeEnum
CollisionType_All 
CollisionType_Plane*£
ActionFaceOptionEnum
ActionFaceOption_None 
ActionFaceOption_NoChange!
ActionFaceOption_FaceAttacker
ActionFaceOption_Indentical
ActionFaceOption_Left
ActionFaceOption_Right
ActionFaceOption_Hero
ActionFaceOption_Reverse
ActionFaceOption_AITarget*¥
ChangePosOptionEnum
ChangePosOption_None 
ChangePosOption_Self
ChangePosOption_Hero
ChangePosOption_Stage
ChangePosOption_Skyline
ChangePosOption_Real
ChangePosOption_Owner
ChangePosOption_RandomX
ChangePosOption_Boss
ChangePosOption_AITarget	*ò
RoleTypeEnum
RoleType_Default 
RoleType_Normal
RoleType_UnCountable
RoleType_SP_Recover
RoleType_Coin
RoleType_Diamond
RoleType_Bullet
RoleType_Obstacle
RoleType_HP_Recover
RoleType_WarpPoint*Á
SpecTargetEnum
SpecTargetNone 
SpecTargetOwner
SpecTargetHero
SpecTargetObstacle
SpecTargetOwnerHero
SpecTargetAll
SpecTargetNoSelf
SpecTargetNoObstacle*h
AttackTypeEnum
AttackType_Hit 
AttackType_None
AttackType_AddHp
AttackType_AddHpALL*—
InputCmdEnum
INPUT_CMD_NONE 
	INPUT_TAP
INPUT_SLIDE_FORWARD
INPUT_SLIDE_UP
INPUT_SLIDE_DOWN
INPUT_UI_CLICK
INPUT_UI_DOWN
INPUT_UI_UP
INPUT_SLIDE_BACK
INPUT_DOUBLE_UI_TAP7
INPUT_HOLD_STARTe
INPUT_HOLD_ENDf
INPUT_PROTRAIT_BUTTONÈ 
INPUT_PROTRAIT_SLIDE_FORWADÊ
INPUT_PROTRAIT_SLIDE_UPË
INPUT_PROTRAIT_SLIDE_DOWNÌ
INPUT_MULTI_CLICK
INPUT_SPEC_FORWARDö
INPUT_SPEC_BACKWARDü
INPUT_SPEC_DOWNú
INPUT_SPEC_UPû
INPUT_CMD_AUTOè
INPUT_CARD_CMD_10001‘N
INPUT_CARD_CMD_10002’N
INPUT_CARD_CMD_10003“N
INPUT_CARD_CMD_10004”N
INPUT_CARD_CMD_10005•N
INPUT_CARD_CMD_10006–N
INPUT_CARD_CMD_10007—N
INPUT_CARD_CMD_10008˜N
INPUT_CARD_CMD_10009™N
INPUT_CARD_CMD_10010šN
INPUT_CARD_CMD_10011›N
INPUT_CARD_CMD_10012œN
INPUT_CARD_CMD_10013N
INPUT_CARD_CMD_10014N
INPUT_CARD_CMD_10015ŸN
INPUT_CARD_CMD_10016 N
INPUT_CARD_CMD_10017¡N
INPUT_CARD_CMD_10018¢N
INPUT_CARD_CMD_10019£N
INPUT_CARD_CMD_10020¤N
INPUT_CARD_CMD_10021¥N
INPUT_CARD_CMD_10022¦N
INPUT_CARD_CMD_10023§N
INPUT_CARD_CMD_10024¨N
INPUT_CARD_CMD_10025©N
INPUT_CARD_CMD_10026ªN
INPUT_CARD_CMD_10027«N
INPUT_CARD_CMD_10028¬N
INPUT_CARD_CMD_10029­N
INPUT_CARD_CMD_10030®N
INPUT_CARD_CMD_10031¯N
INPUT_CARD_CMD_10032°N
INPUT_CARD_CMD_10033±N
INPUT_CARD_CMD_10034²N
INPUT_CARD_CMD_10035³N
INPUT_CARD_CMD_10036´N
INPUT_CARD_CMD_10037µN
INPUT_CARD_CMD_10038¶N
INPUT_CARD_CMD_10039·N
INPUT_CARD_CMD_10040¸N
INPUT_CARD_CMD_10041¹N
INPUT_CARD_CMD_10042ºN
INPUT_CARD_CMD_10043»N
INPUT_CARD_CMD_10044¼N
INPUT_CARD_CMD_10045½N
INPUT_CARD_CMD_10046¾N
INPUT_CARD_CMD_10047¿N
INPUT_CARD_CMD_10048ÀN
INPUT_CARD_CMD_10049ÁN
INPUT_CARD_CMD_10050ÂN
INPUT_CARD_CMD_10051ÃN
INPUT_CARD_CMD_10052ÄN
INPUT_CARD_CMD_10053ÅN
INPUT_CARD_CMD_10054ÆN
INPUT_CARD_CMD_10055ÇN
INPUT_CARD_CMD_10056ÈN
INPUT_CARD_CMD_10057ÉN
INPUT_CARD_CMD_10058ÊN
INPUT_CARD_CMD_10059ËN
INPUT_CARD_CMD_10060ÌN
INPUT_CARD_CMD_10061ÍN
INPUT_CARD_CMD_10062ÎN
INPUT_CARD_CMD_10063ÏN
INPUT_CARD_CMD_10064ĞN
INPUT_CARD_CMD_10065ÑN
INPUT_CARD_CMD_10066ÒN
INPUT_CARD_CMD_10067ÓN
INPUT_CARD_CMD_10068ÔN
INPUT_CARD_CMD_10069ÕN
INPUT_CARD_CMD_10070ÖN
INPUT_CARD_CMD_10071×N
INPUT_CARD_CMD_10072ØN
INPUT_CARD_CMD_10073ÙN
INPUT_CARD_CMD_10074ÚN
INPUT_CARD_CMD_10075ÛN
INPUT_CARD_CMD_10076ÜN
INPUT_CARD_CMD_10077İN
INPUT_CARD_CMD_10078ŞN
INPUT_CARD_CMD_10079ßN
INPUT_CARD_CMD_10080àN
INPUT_CARD_CMD_10081áN
INPUT_CARD_CMD_10082âN
INPUT_CARD_CMD_10083ãN
INPUT_CARD_CMD_10084äN
INPUT_CARD_CMD_10085åN
INPUT_CARD_CMD_10086æN
INPUT_CARD_CMD_10087çN
INPUT_CARD_CMD_10088èN
INPUT_CARD_CMD_10089éN
INPUT_CARD_CMD_10090êN
INPUT_CARD_CMD_10091ëN
INPUT_CARD_CMD_10092ìN
INPUT_CARD_CMD_10093íN
INPUT_CARD_CMD_10094îN
INPUT_CARD_CMD_10095ïN
INPUT_CARD_CMD_10096ğN
INPUT_CARD_CMD_10097ñN
INPUT_CARD_CMD_10098òN
INPUT_CARD_CMD_10099óN
INPUT_CARD_CMD_10100ôN*J
	AudioType
AudioType_None 
AudioType_Voice
AudioType_Effect*í
AddEffectBehindRoleOptionEnum
AddEffect_NOT_BEHIND 
AddEffect_BEHIND_ROLE
AddEffect_FULL_SCREEN_LAYER#
AddEffect_FULL_SCREEN_TOP_FRONT
AddEffect_GROUND_BEHIND
AddEffect_IN_ROLE_UP
AddEffect_IN_ROLE_DOWN*S
ControlRoleTypeEnum
ControlRoleType_ADDROLE 
ControlRoleType_CHARACTERID*Œ
SceneFadeEnum
SceneFade_All 
SceneFade_Card
SceneFade_TEAM_1
SceneFade_TEAM_2
SceneFade_TEAM_ALL
SceneFade_TEAM_SELF
SceneFade_TEAM_TARGET
SceneFade_TEAM_SELF_TARGET
SceneFade_TEAM_SELF_TEAM
SceneFade_TEAM_TARGET_TEAM	bproto3
Í
google/protobuf/any.protogoogle.protobuf"&
Any
type_url (	
value (Bo
com.google.protobufBAnyProtoPZ%github.com/golang/protobuf/ptypes/any¢GPBªGoogle.Protobuf.WellKnownTypesbproto3

	api.protogoogle/protobuf/any.protooptions.proto" 
ResponseStringMsg
msg (	"
NoneResponse"7
	ApiHealth:*Òó
HealthViewÊó/health/Úóå¥åº·æ£€æŸ¥"u
ApiInfo
get
settings (	:QÒóInfoViewÊó/info/,/Info/Úó0æ­¤æœåŠ¡å™¨çš„åŸºæœ¬ä¿¡æ¯å’Œä¸€äº›è°ƒè¯•ä¿¡æ¯"J

ApiInfoRes:<ÒóInfoResViewÊó
/info/res/Úóæ­¤æœåŠ¡å™¨çš„èµ„æºä¿¡æ¯"z
	ApiStatus
status (%
result
status (
msg (	:6Òó
StatusViewÊó/status/,/Status/ÚóæœåŠ¡å™¨çŠ¶æ€"S
ApiMyIp
get
get (	:4ÒóViewMyIpÊó/myip/Úóè·å–è®¿é—®è€…çš„ipåœ°å€"±
ApiDbAccessh
result/
result (2.ApiDbAccess.result.ResultEntry-
ResultEntry
key (
value (:8:8ÒóApiDbAccessViewÊó/db-access/Úódbè®¿é—®æƒ…å†µàó"5
ApiViewTest:&ÒóViewTestÊó/test/Úóæµ‹è¯•æ¥å£"M
	ApiIsOpen:@Òó
IsOpenViewÊó/isopen/,/IsOpen/ÚóæœåŠ¡å™¨æ˜¯å¦å¼€å¯àó"Ñ
Response200&
debug (2.Response200.DebugEntry$
result (2.google.protobuf.AnyB

DebugEntry
key (	#
value (2.google.protobuf.Any:8:0Úó,æ‰€æœ‰httpè¿”å› 200 çš„ä½¿ç”¨è¿™ä¸ªresponse"¹
ResponseClientNotify)
notify (2.ResponseClientNotify.msg9
msg
code (	

ban_reason (	
ban_time (:;Úó7è¿”å›å®¢æˆ·ç«¯çš„å¯¹è¯æ¡†(507)æˆ–è€…ä¸­å¤®æ¨ªå¹…(509)"t
ResponseKick 
kick (2.ResponseKick.Kick
Kick
code (	:,Úó(510,è¸¢æ‰å®¢æˆ·ç«¯ï¼Œéœ€è¦é‡æ–°ç™»é™†"s
PageInfo
prev (
next (
total (
perPage (:+Úó'æ‰€æœ‰å¯åˆ†é¡µçš„å¯ä»¥ä½¿ç”¨æ­¤ç»“æ„"’
ApiModelDetailInfo:|Úóè¿”å›æŒ‡å®šmodelçš„ä¿¡æ¯ÒóModelDetailInfoViewÊóC/models/,/models/<string:name>/,/models/<string:name>/<string:key>/"”
ApiCheckPlayerData
result
data (	:fÚó!æ£€æŸ¥ç©å®¶çš„æ•°æ®æ˜¯å¦åˆæ³•ÒóCheckPlayerDataViewÊó&/checkdata/<string:name>/<string:key>/bproto3
î
google/protobuf/api.protogoogle.protobuf$google/protobuf/source_context.protogoogle/protobuf/type.proto"
Api
name (	(
methods (2.google.protobuf.Method(
options (2.google.protobuf.Option
version (	6
source_context (2.google.protobuf.SourceContext&
mixins (2.google.protobuf.Mixin'
syntax (2.google.protobuf.Syntax"Õ
Method
name (	
request_type_url (	
request_streaming (
response_type_url (	
response_streaming ((
options (2.google.protobuf.Option'
syntax (2.google.protobuf.Syntax"#
Mixin
name (	
root (	Bu
com.google.protobufBApiProtoPZ+google.golang.org/genproto/protobuf/api;api¢GPBªGoogle.Protobuf.WellKnownTypesbproto3
œ
aq.protooptions.protocommon.proto	sec.proto"ß
ApiAQLog
log_type ('
round_start (2.SecRoundStartFlow#
	round_end (2.SecRoundEndFlow*
round_end_count (2.SecRoundEndCount
result
result (:-Òó	AQLogViewÊó/aq/log/Úóå®‰å…¨æ—¥å¿—àóbproto3
ø	
asyncpvp.protocommon.protooptions.protogamedb.proto"ª
DBAsyncPvpPlayerActors
	player_id (	
level (
max_win_cnt (
name (	
battle_actors (2.STActor

head_photo (
social_picture	 (	"š
DBAsyncPvpBattleRecord
	player_id (	;
battle_record (2$.DBAsyncPvpBattleRecord.BattleRecord¯
BattleRecord&
target_battle_actors (2.STActor
is_win (
name (	
level (
uid (	

head_photo (
social_picture	 (	
	player_id
 (	"á
ApiAsyncPvpMatch
index ({
result
index (:
target_actor (2$.DBAsyncPvpBattleRecord.BattleRecord&

res_change (2.STResourceDynamic:AÒóAsyncPvpMatchViewÊó/asyncpvpmatch/Úóå¼‚æ­¥pvpæ‰‹åŠ¨åŒ¹é…"e
ApiInitAsyncPvpRobot:MÒóAsyncPvpRobotViewÊó/asyncpvprobot/Úó!å¼‚æ­¥pvpåŒ¹é…æœºå™¨äººåˆå§‹åŒ–"ã
ApiAsyncPvpRecordInfo€
result
today_max_win_num (
left_rewards_win_num ((
 next_refresh_syncpvp_record_time (
can_refresh (:GÒóAsyncPvpRecordInfoViewÊó/asyncpvprecord/Úóå¼‚æ­¥pvpç›¸å…³è®°å½•"·
ApiAsyncPvpTargetDetail>
result4
result (2$.DBAsyncPvpBattleRecord.BattleRecord:\ÒóAsyncPvpTargetDetailViewÊó#/asyncpvptargetdetail/<string:uid>/Úóå¼‚æ­¥pvpå¯¹æ‰‹è¯¦æƒ…bproto3
ú
auto_fight.protooptions.protocommon.protogamedb.proto"‘
DbAutoFightConf
	player_id (	7
baned_skills (2!.DbAutoFightConf.BanedSkillsEntry2
BanedSkillsEntry
key (	
value (:8"
ApiAutoFightConf@
configure_skills (2&.ApiAutoFightConf.ConfigureSkillsEntry6
ConfigureSkillsEntry
key (	
value (:8}
result?
baned_skills (2).ApiAutoFightConf.result.BanedSkillsEntry2
BanedSkillsEntry
key (	
value (:8:ÒóApiAutoFightConfViewÊó/auto-fight-conf/Úó_post: è‡ªåŠ¨æˆ˜æ–—ç¦ç”¨/å¯ç”¨æŠ€èƒ½ï¼Œ1-ç¦ç”¨ï¼Œ0-å¯ç”¨;get: æŸ¥è¯¢è‡ªåŠ¨æˆ˜æ–—ç¦ç”¨é…ç½®bproto3
ó
burstlinkpvp.protooptions.proto
xlsx.protofight.protodungeon.protogamedb.protoxlsx_server.protocommon.proto"Ï
DbBurstLinkPVPInfo
	player_id (	
is_robot (
expire_seconds (/
	base_info (2.DbBurstLinkPVPInfo.BaseInfo1

match_info (2.DbBurstLinkPVPInfo.MatchInfo5
balance_info (2.DbBurstLinkPVPInfo.BalanceInfo3
actors_info (2.DbBurstLinkPVPInfo.ActorsInfoœ

ActorsInfo
rand_main_actor (
cur_main_actor (
rand_assistant_actor (
cur_assistant_actor (
battle_actor_group (G
player_actors (20.DbBurstLinkPVPInfo.ActorsInfo.PlayerActorsEntry=
PlayerActorsEntry
key (
value (2.STActor:8r
BaseInfo
create_time (
	quit_time (
name (	
level (
fail_num (
win_num (i
	MatchInfo
match_start_time (

pvp_status (
battle_room_uid (
match_proc_id (.
BalanceInfo
isWin (
balanced ("û
ApiBurstLinkPVPInfoš
result/
	base_info (2.DbBurstLinkPVPInfo.BaseInfo#
reward (2.BurstLinkPVPReward&

res_change (2.STResourceDynamic

dungeon_id (:GÒóBurstLinkPVPInfoViewÊó/BurstLinkPvpInfo/Úóè¶…é¢‘è¿çº¿pvpä¿¡æ¯"œ
ApiBurstLinkPVPRandActor
	actor_uid ({
result

rand_actor (2.STActor
cur_main_actor (
cur_assistant_actor (
select_actor (2.STActor:pÒóBurstLinkPVPRandActorViewÊóB/BurstLinkPVPRandActorInfo/<int:kind>/,/BurstLinkPVPRandActorInfo/Úó	pvpåŠŸèƒ½"„
ApiBurstLinkPvpActor!
result
actor (2.STActor:IÒóBurstLinkPvpActorViewÊó/BurstLinkPvpActor/Úópvpæˆ˜æ–—å¯é€‰è§’è‰²"ÿ
ApiBurstLinkPvpBattleGroup:
members (2).DBGamePlayer.ActorGroupInfo.Group.MemberB
actor_groups (2,.ApiBurstLinkPvpBattleGroup.ActorGroupsEntryV
ActorGroupsEntry
key (1
value (2".DBGamePlayer.ActorGroupInfo.Group:8<
result2
result (2".DBGamePlayer.ActorGroupInfo.Group:KÒóBurstLinkPvpBattleGroupViewÊó/BurstLinkPvpActorGroup/Úópvpæˆ˜æ–—ç»„"»
ApiBurstLinkDunBalanceX
result
fail_num (
win_num (
isWin (
rewards (2.STResource:GÒóBurstLinkDunBalanceViewÊó/BurstLinkDunBalance/Úópvpå‰¯æœ¬ç»“ç®—"
ApiBurstLinkPVPBalance0
result&

res_change (2.STResourceDynamic:AÒóBurstLinkPVPBalanceViewÊó/BurstLinkPvpBalance/Úó	pvpç»“ç®—"¨
ApiBurstLinkResult
fail_num (
win_num (+
result
fail_num (
win_num (:BÒóBurstLinkResultViewÊó/BurstLinkPVPResult/Úópvpèƒœè´Ÿåœºæ•°bproto3
š

chat.protocommon.protooptions.protoxlsx_server.proto"{
ApiChat
content (	
channel (
	target_id (	
world_sub_channel (: ÒóChatViewÊó/chat/ÚóèŠå¤©"°
ApiRecentContact+
recent
	sender_id (	
sender (	2
result(
result (2.ApiRecentContact.recent:;ÒóRecentContactViewÊó/recentcontact/Úóæœ€è¿‘è”ç³»äºº"m
ApiBroadCastMessage
text (	
type (::ÒóBroadcastMessageViewÊó/messagebroad/Úóé€šçŸ¥å¹¿æ’­"V
DbDanmu
content (	
color (
time (

id (
	player_id (	".
DbDanmuInfo
channel (	
second ("¢
ApiDanmu
content (	
channel (	
second (
post
post (2.DbDanmu 
get
get (2.DbDanmuInfo:¡ÒóApiDanmuViewÊó{/danmu/,/danmu/<string:channel>/,/danmu/<string:channel>/<int:second>/,/danmu/<string:channel>/<int:second>/<int:danmu_id>/Úóå¼¹å¹•ô àó"f
ApiDanmuInfo 
get
get (2.DbDanmuInfo:4ÒóApiDanmuInfoViewÊó/danmu-info/Úóå¼¹å¹•ä¿¡æ¯"F
	DbBanChat
	player_id (	
ban_stat (
ban_end_time ("ã

ApiBanChat
	player_id (	
ban_stat (
ban_end_time (S
result
result (
	player_id (	
ban_stat (
ban_end_time (:EÒóApiBanChatViewÊó%/banchat/,/banchat/<string:playerId>/Úóç¦è¨€"r
ApiDanmuSwitch&
result
result (2.DanmuSwitch:8ÒóApiDanmuSwitchViewÊó/danmu-switch/Úóå¼¹å¹•å¼€å…³bproto3
Ù
check.protooptions.protodungeon.protofight.proto"¸
ApiPveDungeonCheck 
dungeon_info (2
.DbDungeon
result
ret (:iÒóPveDungeonCheckViewÊó2/PveDungeonCheck/<int:dungeon_uid>/<int:strategy>/Úópveå‰¯æœ¬åä½œå¼Šæ£€æŸ¥"§
ApiFightCheckConfig
IOS (	
Android (	*
get#
config (2.DbFightCheckConfig
post
result (:.ÒóFightCheckConfigViewÊó/FightCheckConfig/"Z
ApiFightCheckDungeon:BÒóFightCheckDungeonViewÊó%/FightCheckDungeon/<int:dungeon_uid>/"Õ
DbFightCheckConfigD
fightcheck_config (2).DbFightCheckConfig.FightcheckConfigEntry™
PlatformVersionsO
version_config (27.DbFightCheckConfig.PlatformVersions.VersionConfigEntry4
VersionConfigEntry
key (	
value (:8]
FightcheckConfigEntry
key (3
value (2$.DbFightCheckConfig.PlatformVersions:8bproto3
Ç
clawcrane.protooptions.protocommon.proto"<
DbClawCraneGlobal
supply_time (

cur_season ("Œ
DbClawCranePlayer>
peripheral_record (2#.DbClawCranePlayer.PeripheralRecord;
unique_record (2$.DbClawCranePlayer.UniqueRecordEntry
current_season (
delivery_address (	I
PeripheralRecord
get_time (#
peripheral_res (2.STResource"
UniquePrizeRecord
count (Y
UniqueRecordEntry
key (3
value (2$.DbClawCranePlayer.UniquePrizeRecord:8"ô
ApiClawCranePrize‡
result<

limit_info (2(.ApiClawCranePrize.result.LimitPrizeInfo?
LimitPrizeInfo

id (

left_count (
stock (:UÒóApiClawCranePrizeViewÊó/ClawCranePrize/Úó$æŠ“å¨ƒå¨ƒé™é‡å¥–åŠ±ä¿¡æ¯ï¼ˆgetï¼‰"·
ApiClawCraneRecordH
result>
peripheral_record (2#.DbClawCranePlayer.PeripheralRecord:WÒóApiClawCraneRecordViewÊó/ClawCraneRecord/Úó$æŠ“å¨ƒå¨ƒè·å¾—å‘¨è¾¹è®°å½•ï¼ˆgetï¼‰"—
ApiClawCraneBingo<
result&

res_change (2.STResourceDynamic

id (:DÒóApiClawCraneBingoViewÊó/ClawCraneBingo/ÚóæŠ“å¨ƒå¨ƒï¼ˆpostï¼‰"ö
ApiClawCraneVerify
count (
reset_record (
reset_counter (
supply_limit (
need_record (¤
resultA
verify_count (2+.ApiClawCraneVerify.result.VerifyCountEntry:
verify_time (2%.ApiClawCraneVerify.result.VerifyTime}
VerifyCount
day (
stock (
force_count (
res_id (
total_count (
force_drop_count (@

VerifyTime
verify_start_time (
verify_end_time (Z
VerifyCountEntry
key (5
value (2&.ApiClawCraneVerify.result.VerifyCount:8:RÒóApiClawCraneVerifyViewÊó/ClawCraneVerify/ÚóæŠ“å¨ƒå¨ƒæ¦‚ç‡éªŒè¯ï¼ˆpostï¼‰bproto3
»	
collections.protocommon.protooptions.protogamedb.proto"Á
ApiCollectionsActorj
result5
actors (2%.ApiCollectionsActor.result.ActorInfo)
	ActorInfo

id (
add_time (:>ÒóCollectionsActorViewÊó/CollectionsActor/Úóæ”¶é›†è§’è‰²"µ
ApiCollectionsEquip^
result5
equips (2%.ApiCollectionsEquip.result.EquipInfo
	EquipInfo
equip_id (:>ÒóCollectionsEquipViewÊó/CollectionsEquip/Úóæ”¶é›†è£…å¤‡"¯
ApiCollectionsNovel
	new_cover (Ÿ
result5
novels (2%.ApiCollectionsNovel.result.NovelInfoA
novel_detail (2+.ApiCollectionsNovel.result.NovelDetailInfo&

res_change (2.STResourceDynamic`
	NovelInfo
novel_id (
cur_reward_num (
can_get_reward (
	cur_cover (>
NovelActorInfo
actor_id (
star (
time (Q
NovelDetailInfo>

actor_info (2*.ApiCollectionsNovel.result.NovelActorInfo:cÒóCollectionsNovelViewÊó4/CollectionsNovel/,/CollectionsNovel/<int:novel_id>/Úóæ”¶é›†ä¹¦å†Œé›†"Ä
ApiNovelCoverView`
result7

cover_info (2#.ApiNovelCoverView.result.CoverInfo
	CoverInfo
cover_id (:MÒóNovelCoverViewÊó/NovelCover/<int:novel_id>/Úóæ”¶é›†ä¹¦å†Œé›†çš„å°é¢bproto3
Ü	
comment.protocommon.protooptions.proto"s
	DbComment

id (
content (	
sender (	
create_time (

like_count (
actor_id ("¿
DbCommentPlayerInfo
	player_id (	
comment_mine (
comments_like (?
comment_record (2'.DbCommentPlayerInfo.CommentRecordEntry

actor_like (@
record
last_comment_time (
today_commnet_count (Q
CommentRecordEntry
key (*
value (2.DbCommentPlayerInfo.record:8"“
DbCommentPool
actorId (1

most_likes (2.DbCommentPool.most_like_info
tops (0
most_like_info

id (

like_count ("1
DbActorLike
actor_id (
like_num ("¦

ApiComment
like (
content (	

like_count (

always_top (g
result
info (2
.DbComment
like (
sender_name (	
rank (

always_top (:hÒóApiCommentViewÊóB/comment/<int:actor_id>/,/comment/<int:actor_id>/<int:comment_id>/Úóè§’è‰²è¯„è®º"ü
ApiActorLike
	is_cancel ((
get
like_num (
is_like (9
post
result (
like_num (
is_like (:tÒóApiActorLikeViewÊó/ActorLike/<int:actor_id>/Úó>è§’è‰²ç‚¹èµ, get-è·å–è§’è‰²ç‚¹èµæƒ…å†µï¼Œpost-è§’è‰²ç‚¹èµbproto3
¢²
common.protooptions.proto"7
STAttr
type (
value (
is_ratio ("5
STSkill

id (
level (
is_read ("5

TalentInfo
	talent_id (
talent_value ("“
STActor

id (
cur_exp (
level (
star (
uid (
quality (-
body_equips (2.STActor.BodyEquipsEntry
skills	 (2.STSkill
attrs
 (2.STAttr
equip_attrs (2.STAttr
special_attr_ids (
is_new (
is_new_card ( 
talent_info (2.TalentInfo
is_lock ()
	group_tag (2.STActor.GroupTagEntry
last_calc_attr_time (
talent_reset_pos (&
talent_reset_info (2.TalentInfo
count_delta (

actor_skin (
default_index (
	is_normal (1
BodyEquipsEntry
key (	
value (:8/
GroupTagEntry
key (	
value (:8"Y

STResource
type (

id (
count (
param (
count_delta ("u
STItem

id (
count (
got_time (
is_new (
expire_time (
history_get_count ("L
STMedal

id (
got_time (
is_new (
expire_time ("7
STEmoji

id (
got_time (
is_new (";
STSignboard

id (
got_time (
is_new (";
STHeadPhoto

id (
got_time (
is_new ("<
STNovelCover

id (
got_time (
is_new ("Ø
STEquip
uid (

id (
cur_exp (
level (
	actor_uid (
is_lock	 (
is_new (
count_delta (
random_attrs (2.STAttr
rand_attr_index (
match_index ("Õ
STResourceDynamic
base_res (2.STResourceH 
actor (2.STActorH 
equip (2.STEquipH 
multiple (
	profit_id (
extra ("
in_box (2.STResourceDynamicB
ResourceDynamic"—
STResourceRecord
res (2.STResource
level (
rand_attr_index (
rand_attr_value_index (
star (
talent_value ("3
ResDropFloorScore
channel (
score ("P
EnterCondition
finishedChapterId (#
finishedChapterIdIsOptional ("µ
GameAccountSwitch
qq_login_switch (
wx_login_switch (
guest_login_switch (!
wx_scan_code_login_switch (
bulletin_switch (
custom_service ("2
ResPack
type (	
keys (	
res (*æ!
ResourceChangeReason
ResChangeReasonNone 
ResChangeReasonGm
ResChangeReasonCreatePlayer
ResChangeReasonDungeonOpen!
ResChangeReasonDungeonBalance!
ResChangeReasonActorDecompose
ResChangeReasonActorLevelup
ResChangeReasonActorStarup+
'ResChangeReasonActorMixObtainSkillPoint	
ResChangeReasonShopBuy

ResChangeReasonShopRefound
ResChangeReasonGachaOpen
ResChangeEquipEnhance
ResChangeEquipRecast 
ResChangePowerIncreaseByTime
ResChangeEquipDecompose
ResChangeReasonShopRefresh#
ResChangeReasonPurchaseFinished%
!ResChangeReasonActorReplaceTalent$
 ResChangeReasonActivePointReward!
ResChangeReasonTaskDoneReward
ResChangeReasonChatCost(
$ResChangeReasonAchievementDoneReward
ResChangeReasonSkillLevelup!
ResChangeReasonMailAttachment
ResChangeReasonGuildCreate
ResChangeReasonGuildDonate
ResChangeReasonTournament
ResChangeReasonDailySignin
ResChangeReasonSigninAcc*
&ResChangeReasonTournamentMysteryReward"
ResChangeReasonGetRelaionPoint 
ResChangeReasonTowerWipe!
ResChangeReasonTowerBalance"#
ResChangeReasonTowerScoreReward#&
"ResChangeReasonTournamentWinReward$$
 ResChangeReasonChapterStarReward%
ResChangeReasonBuyRes&"
ResChangeReasonAsyncPvpRefresh'&
"ResChangeReasonPlayerLevelupReward("
ResChangeReasonActorInheriting)$
 ResChangeReasonDeployMissionCost*&
"ResChangeReasonDeployMissionReward+'
#ResChangeReasonDeployMissionSpeedUp,"
ResChangeReasonResourceCompose-
ResChangeReasonTalentReset. 
ResChangeReasonDeployDiscard/
ResChangeReasonItemBoxUse0%
!ResChangeReasonCollectNovelReward1 
ResChangeReasonItemProfitUse2
ResChangeReasonGiftCodeUse3!
ResChangeReasonCommandGiftGot4&
"ResChangeReasonDungeonStarExchange5 
ResChangeReasonItemFavourUse6
ResChangeReasonGachaShopBuy7$
 ResChangeReasonGachaPointRefresh8
ResChangeReasonAutoBox9
ResChangeReasonMall:
ResChangeReasonMonthCard;
ResChangeReasonSealCost<
ResChangeReasonSealReward=
ResChangeReasonPvpRefresh>
ResChangeReasonDailyRefresh?
ResChangeReasonPVPReward@
ResChangeReasonIDIPA
ResChangeReasonCongratsB
ResChangeRolePresentC!
ResChangeReasonRacePvpRefreshD
ResChangeReasonExpiredE#
ResChangeReasonRecePVPSegRewardF
ResChangeReasonVoteRoleCostG!
ResChangeReasonVoteRoleRewardH'
#ResChangeReasonVoteRoleTargetRewardI
ResChangeReasonGuildModifyJ
ResChangeReasonNoticeK'
#ResChangeReasonVoteRoleAutoExchangeL
ResChangePlayerRenameN%
!ResChangeReasonGachaOpenTimeLimitO%
!ResChangeReasonGachaOpenActorChipP!
ResChangeReasonSigninGiftMailQ"
ResChangeReasonDelayRewardMailR$
 ResChangeReasonWeeklyShareRewardS,
(ResChangeReasonFirstGoldActorShareRewardT'
#ResChangeReasonContinuousLoginPrizeU
ResChangeReasonPostcardCostV 
ResChangeReasonPostcardPrizeW&
"ResChangeReasonGachaOpenFriendshipX
ResChangeReasonItemSellY
ResChangeReasonWebSigninZ
ResChangeReasonWebVoteRole[)
%ResChangeReasonEventDungeonStarReward\
ResChangeReasonWeekRefresh]*
&ResChangeReasonTriggeredEventTicketUse^ 
ResChangeReasonFavourLevelUp_!
ResChangeReasonActivitySignin`)
%ResChangeReasonMailEventDungeonRewarda 
ResChangeReasonMazeEnterCostb!
ResChangeReasonMazeEventPrizec
ResChangeReasonMazePrized"
ResChangeReasonConditionRewarde(
$ResChangeReasonWeekActivePointRewardf,
(ResChangeReasonWeekActivePointRewardMailg
ResChangeReasonPowerBuyh
ResChangeReasonCoinBuyi
ResChangeReasonClawCranej)
%ResChangeReasonChallengeDungeonRewardk'
#ResChangeReasonChallengeDungeonMaill!
ResChangeReasonEveryDaySigninm#
ResChangeReasonJigsawPuzzleCostn$
 ResChangeReasonJigsawPuzzlePrizeo"
ResChangeReasonDungeonOverFlowp
ResChangeReasonCarnival91q1
-ResChangeReasonActorCultivationProgressRewards*
&ResChangeReasonActorCultivationUpgradet*
&ResChangeReasonGuildDungeonScoreRewardu-
)ResChangeReasonGuildDungeonProgressRewardv/
+ResChangeReasonGuildDungeonRewardGuildScorew
ResChangeReasonPerfectGachax$
 ResChangeReasonActivityShopGoodsy.
*ResChangeReasonExtremeChallengeFloorRewardz"
ResChangeReasonActorProficient{-
)ResChangeReasonMiniGameJoinRewardEveryday|
ResChangeReasonCostDeltN*ô
PushType
PushType_None 
PushType_BaseResChange
PushType_BaseInfoChange
PushType_ItemChange
PushType_ActorChange 
PushType_BattleInstanceStart
PushType_EquipChange
PushType_ActorDelete
PushType_EquipDelete!
PushType_BattleRoomInfoChange	"
PushType_FightActiveDataChange

PushType_ActorNew
PushType_EquipNew
PushType_ActorChangeAll)
%PushType_DungeonChapterProgressChange
PushType_ActorGroupChange
PushType_AchievementChange
PushType_AchievementDone
PushType_GachaCountRefresh
PushType_NewPrivateMsg
PushType_GuildPlayerChange
PushType_GuildChange
PushType_GuildMemberChange"
PushType_GuildInvitationChange%
!PushType_GuildPendingMemberChange
PushType_GuildMessage
PushType_GuildSystemMessage
PushType_SigninInfoChange  
PushType_RelationAppyListNew!#
PushType_RelationAppyListDelete""
PushType_RelationFriendListNew#%
!PushType_RelationFriendListDelete$(
$PushType_RelationFriendLoginOrLogout%
PushType_TowerRefreshed*
PushType_AsyncPvpRecord+!
PushType_AsyncPvpDailyRefresh,&
"PushType_RelationFriendInfoChanged-!
PushType_DeployMissionChanged/
PushType_DeployMissionEnd0
PushType_MedalChange1
PushType_NovelCoverChange2
PushType_ProfitChange3"
PushType_GetCommandGiftSuccess4%
!PushType_GuildInvitedMemberChange5
PushType_GuildRecordChange6
PushType_GuildScoreChange7
PushType_GuildSkillChange8
PushType_GuildVoteChange9"
PushType_GuildDonateListChange:
PushType_NotePadUnlockRole;
PushType_PurchaseGoodsNew<
PushType_PurchaseBuyRecord=
PushType_EmojiChange>
PushType_MonologueChange?
PushType_RolePresent@
PushType_BurstLinkPVPMatchA
PushType_RacePVPBalanceB
PushType_HeadPhotoChangeC
PushType_RacePVPMatchFailD
PushType_BurstLinkMatchFailE
PushType_FightSvrErrF 
PushType_DunDoneTimesRefreshG
PushType_TSSAntiDataH
PushType_MessageBoxI
PushType_ShareInfoChangeJ
PushType_TeamDungeonInviteK"
PushType_ContinuousLoginChangeL
PushType_SignboardChangeM
PushType_TriggeredEventAddN!
PushType_TriggeredEventDeleteO,
(PushType_TriggeredEventFinishCountChangeP$
 PushType_NotePadRoleFavourChangeQ"
PushType_RelationPointRedPointR#
PushType_ChipExchangePosRefreshS
PushType_GuildExpChangeT
PushType_FightBuffChangeU
PushType_PerfectGachaSucV'
#PushType_GuildDungeonProgressRewardW(
$PushType_GuildDungeonRedPointRefreshX#
PushType_PlayerGuildScoreChangeY#
PushType_ReceivedGiftFromFriendZ%
!PushType_ReceivedAskReqFromFriend[)
%PushType_DailyExtraRewardCountRefresh\*™
ResourceType
ResNone 
ResPlayerExp
ResPlayerHeadPhoto
ResPlayerTitle
ResPlayerMoney
ResPlayerPower
ResPlayerGold
ResMedal
ResPlayerHonourPoint	
ResPlayerRelationPoint

ResItem
ResEquip
ResPlayerHeadFrame
ResActor
ResGuildScore
ResTowerScore
ResAchievementPoint
ResNovelCover
ResGachaPoint
ResMonthCardDays
ResEmoji
ResMonologue
ResPlayerPayMoney
ResDungeonPlayTimesLimit
ResActorSkin
ResSignboard

ResMax*ò
ActorProduceWay
ActorProduceWay_None 
ActorProduceWay_Init
ActorProduceWay_Compose
ActorProduceWay_GachaBase 
ActorProduceWay_GachaAdvance 
ActorProduceWay_GachaSpecial
ActorProduceWay_Dungeon
ActorProduceWay_SignUp	$
 ActorProduceWay_GachaNormalFloor%
!ActorProduceWay_GachaAdvanceFloor%
!ActorProduceWay_GachaSpecialFloor(
$ActorProduceWay_GachaNormalFirstDropg)
%ActorProduceWay_GachaAdvanceFirstDroph)
%ActorProduceWay_GachaSpecialFirstDropi*s
MailType
MailType_None 
MailType_IDIP
MailType_MonthCard
MailType_ResOver
MailType_Commonc*Í
AttrType
AttrType_None 
AttrType_Hp
AttrType_HpRate
AttrType_Attack
AttrType_None1
AttrType_PhysicalDefense
AttrType_MagicalDefense
AttrType_AttackRate
AttrType_None2 
AttrType_PhysicalDefenseRate	
AttrType_MagicalDefenseRate

AttrType_BuffAccuracyRate
AttrType_BuffResistanceRate
AttrType_CriticalHitRate"
AttrType_CriticalHitDamageRate#
AttrType_CriticalResistanceRate)
%AttrType_CriticalResistanceDamageRate
AttrType_FinalDamage
AttrType_FinalDamageRate
AttrType_AttackNeglect
AttrType_None3
AttrType_SkillHpHealRate
AttrType_Penetration
AttrType_PenetrationRate
AttrType_Speed
AttrType_Max*X
EquipPos
EquipPos_Head 
EquipPos_Armor
EquipPos_Shoe
EquipPos_Cloak*«
CastSkillType
CastSkillType_None 
CastSkillType_BigSkill
CastSkillType_Link
CastSkillType_Normal1
CastSkillType_Normal2
CastSkillType_Assist*µ
EquipQuality
EquipQuality_White 
EquipQuality_Green
EquipQuality_Yellow
EquipQuality_Purple
EquipQuality_Orange
EquipQuality_Red
EquipQuality_Max*µ
ActorQuality
ActorQuality_White 
ActorQuality_Green
ActorQuality_Yellow
ActorQuality_Purple
ActorQuality_Orange
ActorQuality_Red
ActorQuality_Max*F
	ActorType
ActorType_None 
ActorType_Main
ActorType_Sub*U
Category
Category_NULL 
Category_ATK
Category_DEF
Category_ASSIS*ß
DungeonType
DungeonType_None 
DungeonType_Story
DungeonType_Normal
DungeonType_AsyncPvp
DungeonType_Tower
DungeonType_WeekActivity
DungeonType_Training
DungeonType_RacePVP
DungeonType_BurstLinkPvp
DungeonType_Gold	
DungeonType_TeamPve

DungeonType_Challenge
DungeonType_Maze
DungeonType_Triggered
DungeonType_SemiFixed
DungeonType_AssistGuild
DungeonType_ChallengeGuild
DungeonType_TowerNew*
BurstLinkDungeonState
BurstLinkDungeonState_None "
BurstLinkDungeonState_WaitJoin"
BurstLinkDungeonState_WaitLoad+
'BurstLinkDungeonState_Fight_RequestCard'
#BurstLinkDungeonState_Fight_UseCard(
$BurstLinkDungeonState_Fight_RoundEnd
BurstLinkDungeonState_End*½
RacePVPPlayerState
RacePVPPlayerState_None 
RacePVPPlayerState_Fighting
RacePVPPlayerState_WaitNext$
 RacePVPPlayerState_End_NoBalance"
RacePVPPlayerState_End_Balance*‡
ChapterType
ChapterType_None 
ChapterType_Story
ChapterType_Normal
ChapterType_Training
ChapterType_Challenge*Ë
ItemFuncType
ItemFuncType_None 
ItemFuncType_ActorExp
ItemFuncType_EquipExp
ItemFuncType_ActorChips
ItemFuncType_AssistChips
ItemFuncType_DeploySpeedUp
ItemFuncType_Box
ItemFuncType_Star
ItemFuncType_Profit
ItemFuncType_Favour	
ItemFuncType_AutoBox
ItemFuncType_ManualBox*
ManualBoxType
ManualBoxType_Default 
ManualBoxType_Actor
ManualBoxType_ActorSkin
ManualBoxType_Equip
ManualBoxType_Mix*i
GamePlatForm
GamePlatForm_Ios 
GamePlatForm_Android
GamePlatForm_Pc
GamePlatForm_Allc*P
MSDKLoginPlatform
	MSDK_None 
MSDK_Wechat
MSDK_QQ

MSDK_Guest*Ô
PurchasePlatform
PurchasePlatform_none 
PurchasePlatform_ios_cn
PurchasePlatform_android_cn#
PurchasePlatform_android_global
PurchasePlatform_ios_midas"
PurchasePlatform_android_midas*µ
PurchaseChannel
PurchaseChannel_none 
PurchaseChannel_apple
PurchaseChannel_alipay
PurchaseChannel_wechat
PurchaseChannel_google
PurchaseChannel_midas*®
SpecialAttrCondition
SpecialAttrCondition_Hp  
SpecialAttrCondition_BuffNum
SpecialAttrCondition_Round!
SpecialAttrCondition_ActorNum 
SpecialAttrCondition_Faction
SpecialAttrCondition_Seat&
"SpecialAttrCondition_TotalHurtData)
%SpecialAttrCondition_RoundBeHurtValue%
!SpecialAttrCondition_HitTargetNum)
%SpecialAttrCondition_CastSkillSkipped	
SpecialAttrCondition_Mp

SpecialAttrCondition_Sp 
SpecialAttrCondition_AddBuff#
SpecialAttrCondition_RemoveBuff
SpecialAttrCondition_CostMp
SpecialAttrCondition_CostSp 
SpecialAttrCondition_HurtVal#
SpecialAttrCondition_IsCritical$
 SpecialAttrCondition_RefreshBuff*«
Timing
Timing_ActionStart 
Timing_ActionEnd
Timing_FirstAttack
Timing_EveryAttack

Timing_Die
Timing_RoundNumberChange
Timing_RoundTeamStart
Timing_Always
Timing_BuffChange
Timing_FirstAttackAfter	
Timing_EveryAttackAfter

Timing_BuffRefresh*Æ
IdentityCategory
IdentityCategory_Self 
IdentityCategory_SelfTeam
IdentityCategory_Targets
IdentityCategory_TargetTeam
IdentityCategory_Target
IdentityCategory_AllTeam*~
ItemType
TypeNone 
Material

Consumable
	ActorChip
ActivityDrop	
Scene
CultivationCG
Max*é
EChatChannel
EChatChannel_None 
EChatChannel_World
EChatChannel_System
EChatChannel_Private
EChatChannel_Broad
EChatChannel_Guild
EChatChannel_Group
EChatChannel_Event
EChatChannel_XYView*‘
WinType
WinType_None 
WinType_KillAll
WinType_KillSingle
WinType_KillALLInRound
WinType_KillSingleInRound
WinType_NoDeadAll
WinType_NoDeadSingle
WinType_NoDeadAllInRound
WinType_NoDeadSingleInRound
WinType_NoDeadAnyInRound	*„
TaskType
TaskType_None 
TaskType_OnlineFromP0toP1#
TaskType_BalanceP0DungeonMTimes
TaskType_GachaP0MTimes
TaskType_BattleLinkP0MTimes$
 TaskType_ActorIdP0BigSkillMTimes
TaskType_PowerCostM"
TaskType_ActivityP0DunP1MTimes 
TaskType_KillActorIdP0MTimes
TaskType_GoldGetM
TaskType_TowerP0FloorM!
TaskType_TournamentJoinMTimes#
TaskType_BalanceDungeonPnMTimes"
TaskType_AsyncPvpWinMTarget#(
$TaskType_AsyncPvpCurrentMaxWinMCount$
TaskType_LoginMTimes%
TaskType_LevelM&
TaskType_DeployDoneM'
TaskType_RoleP0CollectM()
%TaskType_BalanceP0DungeonMTimesP1Star)*
&TaskType_BalanceP0DungeonMTimesActorPN*
TaskType_CompletePnTask+ 
TaskType_BalanceP0GoldMTimes,
TaskType_EquipOnMTimes-
TaskType_EquipEnhanceMTimes.
TaskType_StarLevelupMTimes/
TaskType_FriendMCount0
TaskType_ShopP0BuyMTimes1
TaskType_MultiPowerMTimes2
TaskType_TalenResetMTimes4"
TaskType_GameCenterLoginMTimes5$
 TaskType_ActorSkillLevelUpMTimes6
TaskType_ActorLevelUpMTimes7
TaskType_JoinGuildMTimes8
TaskType_EnableSkinMTimes9
TaskType_ActorP0LevelUpM:"
TaskType_ActorPnStarP0LevelUpM;&
"TaskType_ActorP0TotalSkillLevelP1M<
TaskType_JoinRacePvPMTimes=
TaskType_UseFuncMTimes> 
TaskType_ActorStarP0LevelUpM?$
 TaskType_TaskActivePointP0MTimes@'
#TaskType_PayTestReChargeRewardLoginA&
"TaskType_EquipEnhanceP0LevelMCountB*
&TaskType_BalancePnDungeonMTimesActorP0C
TaskType_CollectionNovelP0D
TaskType_ActorP0DunTypeP1F)
%TaskType_ActorP0DunActivityTypeP1IdP2G
TaskType_ActorP0DunHpLessP1I"
TaskType_ActorP0DunActorSkinP1J
TaskType_RoleIdP0DeployK
TaskType_SetSignboardRoleP0L
TaskType_ActorP0TalentMN
TaskType_ActorP0LockO
TaskType_ActorP0CommentMP
TaskType_ActorP0LikeQ
TaskType_ItemP0GuildDonateR
TaskType_RoleP0FavourLevelMS"
TaskType_ActorP0ClearBuffEnemyT!
TaskType_ActorP0ClearBuffSelfU#
TaskType_ActorP0KillLastSkillP1V#
TaskType_ActorP0FirstKillP1SeatW'
#TaskType_ActorP0SkillP1EnemyCountP2X
TaskType_ActorP0SkillP1Y
TaskType_ActorP0KillZ
TaskType_ActorP0DunPn[#
TaskType_ActivityP0P1KillP2Role`0
,TaskType_ActivityP0P1BigSkillLessThanTimesP2\1
-TaskType_ActivityP0P1LinkSkillLessThanTimesP2]3
/TaskType_ActivityP0P1AssistSkillLessThanTimesP2^
TaskType_PassDungeonPnAll_/
+TaskType_ActivityP0P1ActorP2FirstKillP3Seata*Ú
UseFuncMTimes
UseFuncMTimes_None 
ViewTeamDetail«

SelectPlayMethod¬

ChangeSortMethod­

TrackMaterials®

BanSkillInAutoFight¼0
ChangeSignBroad½0
FastSelection°

GetRelationPoint±
*Í
DungeonTarget
DungeonTarget_None 
DungeonTarget_LinkSkill
DungeonTarget_Round
DungeonTarget_DeadActor
DungeonTarget_TotalBeHurt
DungeonTarget_BigSkill
DungeonTarget_CardNum
DungeonTarget_KillWinType
DungeonTarget_KillBySkill 
DungeonTarget_KillBySequence	
DungeonTarget_ClearBuff
*Ö
AchievementType
AchievementType_None *
&AchievementType_BalanceP0DungeonMTimes(
$AchievementType_ActorTypeP0ForMTimes!
AchievementType_BookP0ActorP1!
AchievementType_ActorP0StarP1"
AchievementType_ActorP0LevelP1,
(AchievementType_ActorP0TotalSkillLevelP1(
$AchievementType_ActorP0TalentCountP1#
AchievementType_AchievementDone
AchievementType_EquipType	!
AchievementType_SuitEquipType
"
AchievementType_EquipQualityP0
AchievementType_GachaP0
AchievementType_LevelM
AchievementType_Gold&
"AchievementType_BattleLinkP0MTimes+
'AchievementType_ActorIdP0BigSkillMTimes'
#AchievementType_ActorIdP0DeadMTimes
AchievementType_TotalBeHurt#
AchievementType_MaxRoundVictory"
AchievementType_MaxRoundFailed4
0AchievementType_BalanceP0ActivityDungeonP1MTimes!
AchievementType_TowerP0FloorM$
 AchievementType_ScoreLtP1ActorP0&
"AchievementType_BattleLinkP0MTypes 
AchievementType_EquipLevelP0
AchievementType_PowerCostM'
#AchievementType_KillActorIdP0MTimes$
 AchievementType_TaskActivePointM+
'AchievementType_GachaP0IntervalP1MTimes!
AchievementType_GachaP0ActorM )
%AchievementType_GachaP0ActorMTalentP1#'
#AchievementType_ActorP0TalentCountM!&
"AchievementType_AsyncPvpWinMTarget"
AchievementType_DeployDoneM$#
AchievementType_RoleMFavourLvP0% 
AchievementType_UnlockEMojiM&&
"AchievementType_RoleCongratsMTimes'$
 AchievementType_UnlockMonologueM(%
!AchievementType_BurstLinkPVPJoinM-$
 AchievementType_BurstLinkPVPWinM. 
AchievementType_RacePVPJoinM4
AchievementType_RacePVPWinM5!
AchievementType_GachaP0CountM<%
!AchievementType_UseP0KillP1MTimes=1
-AchievementType_ResetGetTalentP0ScoreP1MTimes>+
'AchievementType_ResetGetQualityP0MTimes?,
(AchievementType_ActorSkinQualityP0MTimes@'
#AchievementType_CollectSkinPnMTimesA%
!AchievementType_ActorPnQualityP1MB)
%AchievementType_SurviveP0GroupUseP1HPC%
!AchievementType_GaChaMTimesNoGoldD4
0AchievementType_ResetGreenTalentContinuousMTimesE
AchievementType_ShowCreditF1
-AchievementType_BalanceP0DungeonMTimesActorPNG*¸
TextCheckCategory
TextCheck_None 
TextCheck_Name
TextCheck_Sign
TextCheck_Desc
TextCheck_Chat
TextCheck_Title
TextCheck_Content
TextCheck_Other*]
	BroadType
BroadType_None 
BroadType_System
BroadType_Broad
BroadType_All*o
GuildMemberType
GuildMemberType_Normal !
GuildMemberType_VicePresident
GuildMemberType_President*ª
MiscEnum
MiscEnum_None 
ActorGroupMaxActorCount
PlayerMedalShowMaxNum
PlayerActorDbMaxNumˆ'
PlayerEquipDbMaxNumˆ'
ChipExchangePosMaxCount*ò
ActorGroupType
ActorGroupType_None 
ActorGroupType_Dungeon
ActorGroupType_Tower
ActorGroupType_AsyncPvp
ActorGroupType_Show
ActorGroupType_Aura
ActorGroupType_Gold
ActorGroupType_BurstLinkPvp
ActorGroupType_RacePvp
ActorGroupType_Activity	
ActorGroupType_Triggered

ActorGroupType_Maze
ActorGroupType_Guild*÷
NotePadEventType
NotePadEventType_None 
NotePadEventType_GetActor
NotePadEventType_FavourLvUp!
NotePadEventType_GetChatImage!
NotePadEventType_GetHeadPhoto
NotePadEventType_Monologue
NotePadEventType_Present
NotePadEventType_Congrats$
 NotePadEventType_UnlockActorSkin
NotePadEventType_ClassicAna	
NotePadEventType_Voice

NotePadEventType_Cartoon
NotePadEventType_Postcard
NotePadEventType_Signboard%
!NotePadEventType_ActorCultivation*b
RolePresentType
RolePresentType_None 
RolePresentType_Fixed
RolePresentType_Random*i
RolePresentState
RolePresentState_NotOpen 
RolePresentState_Open
RolePresentState_Present*
TeamDungeonInviteFrom#
TeamDungeonInviteFrom_Strangers  
TeamDungeonInviteFrom_Friend
TeamDungeonInviteFrom_Guild*b
PlayerGameState
PlayerGameState_Normal 
PlayerGameState_Room
PlayerGameState_Fight*Ó
LockInputFlag
Invalid 
Fade
	AsyncLoad
SceneLoading
GameLoading
UITransition
Purchase 
NetworkLoading@
NetworkLoading2€
NetworkLoading3€
GM€
NormalBlock€*x
	Operation
EqualTo 

NotEqualTo
LessThan
LessThanOrEqualTo
GreaterThan
GreaterThanOrEqualTo*P
ResourceParamType
ResourceParamType_None 
ResourceParamType_SyncOnly‘N*€
PlayerOption
PlayerOption_None 
PlayerOption_Friend
PlayerOption_PrivateChat
PlayerOption_Report
PlayerOption_Blacklist
PlayerOption_GotoSpace
PlayerOption_QQFriend 
PlayerOption_Guild@
PlayerOption_RecallFriend€*˜
ActivityType
ActivityType_None 
ActivityType_StarResDungeon"
ActivityType_CollectionDungeon
ActivityType_Tower
ActivityType_Gacha$
 ActivityType_Tournament_AsyncPvp
ActivityType_Tournament_2
ActivityType_Tournament_3
ActivityType_Tournament_4
ActivityType_Tournament_5	
ActivityType_DeployMission

ActivityType_Task
ActivityType_Shop
ActivityType_GachaShop#
ActivityType_GachaProbabilityUp
ActivityType_Mall
ActivityType_Seal
ActivityType_GoldDungeon
ActivityType_BurstLinkPvp
ActivityType_GachaAd
ActivityType_RacePVP
ActivityType_ExtraReward
ActivityType_VoteRole
ActivityType_DungeonTeam
ActivityType_ShareReward 
ActivityType_ContinuousLogin
ActivityType_Postcard
ActivityType_EventDungeon
ActivityType_WebSignin
ActivityType_WebVoteRole
ActivityType_ChallengeMode
ActivityType_TriggeredEvent
ActivityType_TenCheckin !
ActivityType_DiffActorInherit!
ActivityType_Maze" 
ActivityType_ConditionReward#
ActivityType_GachaPlotEmote$#
ActivityType_MallDirectPurchase%
ActivityType_ClawCrane&(
$ActivityType_EventDungeon_Autumn2018',
(ActivityType_EventDungeon_Autumn2018Hard(
ActivityType_ChangeIcon) 
ActivityType_RacePVPActivity*
ActivityType_Theme+"
ActivityType_ActorChipExchange,
ActivityType_CultivationPvP- 
ActivityType_EveryDayCheckin.
ActivityType_JigsawPuzzle0#
ActivityType_GuildAssistDungeon1&
"ActivityType_GuildChallengeDungeon2
ActivityType_Carnival913!
ActivityType_ActorCultivation5
ActivityType_Christmas6'
#ActivityType_CustomActorCultivationE*È
ActivityCategory
ActivityCategory_None #
ActivityCategory_WeeklyActivity
ActivityCategory_Gacha
ActivityCategory_Tournament
ActivityCategory_Shop
ActivityCategory_Dungeon*˜
ActivityStatus
ActivityStatus_None 
ActivityStatus_PreHeat
ActivityStatus_Started
ActivityStatus_Finished
ActivityStatus_Close*ƒ
ActivityTimeType
ActivityTimeType_None 
ActivityTimeType_Forever
ActivityTimeType_Week
ActivityTimeType_Period*‹	
UnlockWindow
UnlockWindow_None 
UnlockWindow_Guild
UnlockWindow_Portal
UnlockWindow_Welfare
UnlockWindow_Task
UnlockWindow_Journey
UnlockWindow_Shop%
!UnlockWindow_ActivityStageChoose2%
!UnlockWindow_ActivityStageChoose1
UnlockWindow_Tower	
UnlockWindow_AsyncPvp

UnlockWindow_ChatRoom
UnlockWindow_Friend
UnlockWindow_Deploy
UnlockWindow_AutoFight
UnlockWindow_GachaChips
UnlockWindow_Challenge
UnlockWindow_MultiPower
UnlockWindow_DungeonGold
UnlockWindow_TaskRoutine
UnlockWindow_TaskMain
UnlockWindow_TaskDeadline
UnlockWindow_TaskRepeat
UnlockWindow_TaskNewbie
UnlockWindow_TaskChallenge
UnlockWindow_TaskFavour
UnlockWindow_BurstLink
UnlockWindow_RacePvp
UnlockWindow_NoticeSystem
UnlockWindow_TalentReset
UnlockWindow_MySpace
UnlockWindow_DungeonTeam
UnlockWindow_TriggeredEvent 
UnlockWindow_FightAgain!
UnlockWindow_RoleNotepad"#
UnlockWindow_EventDungeonSummer#(
$UnlockWindow_EventDungeon_Autumn2018$,
(UnlockWindow_EventDungeon_Autumn2018Hard% 
UnlockWindow_RacePVPActivity&*¤
RefreshPriceType
RefreshPriceType_None 
RefreshPriceType_ActivityN
RefreshPriceType_power‘N
RefreshPriceType_Gold’N 
RefreshPriceType_SkillPoint“N$
RefreshPriceType_LinkSkillPoint”N+
&RefreshPriceType_AsyncPvpRefreshTarget•N
RefreshPriceType_BurstLink–N
RefreshPriceType_ChgName—N"
RefreshPriceType_DungeonTimes˜N 
RefreshPriceType_DeployTimes
!
RefreshPriceType_ChgNameCard™N*G
ActivePointType
ActivePointType_Daily 
ActivePointType_Theme*ˆ
TaskCategory
TaskCategory_None 
TaskCategory_Routine
TaskCategory_Main
TaskCategory_Deadline
TaskCategory_Repeat
TaskCategory_Newbie
TaskCategory_Challenge
TaskCategory_Favour
TaskCategory_Presentd
TaskCategory_Themef
TaskCategory_Sceneg!
TaskCategory_ActorCultivationh
TaskCategory_ChristmasDailyi 
TaskCategory_ChristmasWeeklyj*g
MedalCategory
	MedalNone 

Favour	
Story
	Challenge	
Other
Special	
Count*y
DeployMissionStatus
MissionStatus_None 
MissionStatus_Start
MissionStatus_Finish
MissionStatus_Close*|
DeployMissionCondition
DeployMissionCondition_None 
DeployMissionCondition_Role 
DeployMissionCondition_Lable*w
	PlayerSex
PlayerSex_None 
PlayerSex_Man
PlayerSex_Woman
PlayerSex_BothSexes
PlayerSex_Unkonwn*Ç
PlayerMessageBoardPermission%
!PlayerMessageBoardPermission_None '
#PlayerMessageBoardPermission_Anyone,
(PlayerMessageBoardPermission_OnlyFriends)
%PlayerMessageBoardPermission_OnlySelf*D
TutorialStatus
TutorialStatus_Undone 
TutorialStatus_Done*L
	WearEquip
WearEquip_None 
WearEquip_TakeOn
WearEquip_TakeOff*`
EServerStatus
EServerStatus_Normal 
EServerStatus_Full
EServerStatus_Maintenance*
ServerOpenStatus
ServerOpenStatus_Normal 
ServerOpenStatus_OpenSoon 
ServerOpenStatus_Maintenance
ServerOpenStatus_Closed*÷

ProfitType
ProfitType_None 
ProfitType_Gold
ProfitType_PlayerExp
ProfitType_ActorExp
ProfitType_ExpItem
ProfitType_StarItem
ProfitType_Lucky
ProfitType_ExtraDrop
ProfitType_VIPNORMAL	
ProfitType_VIPSUPER
 
ProfitType_QQGameCenterLogin 
ProfitType_WXGameCenterLogin
ProfitType_VIPSUPER_GOLD
ProfitType_ItemAdd*<
Tag
Tag_None 
Tag_FrontAttack
Tag_BackAttack*š
SealRewardKind
SealRewardKind_None 
SealRewardKind_Horizontal
SealRewardKind_Vertical
SealRewardKind_Diagonal
SealRewardKind_All*¤
PlayerPVPStatus
PlayerPVPStatus_Normal 
PlayerPVPStatus_Matching
PlayerPVPStatus_Matched
PlayerPVPStatus_Fighting
PlayerPVPStatus_FightEnd*È

SpeechType
SpeechType_None 
SpeechType_Login
SpeechType_Click
SpeechType_Idle
SpeechType_AsyncPvp
SpeechType_BurstLink
SpeechType_RacePvp
SpeechType_ForceTouch*˜
TournamentStatus$
 TournamentStatus_NoParticipation "
TournamentStatus_Participation
TournamentStatus_Group
TournamentStatus_Group_End*Z
RacePVPFactor
RacePVPFactor_no 
RacePVPFactor_seg 
RacePVPFactor_sub_segN*s
RacePVPStatus
RacePVPStatus_None 
RacePVPStatus_Match
RacePVPStatus_Fight
RacePVPStatus_Settle*[
ActivityCycle
ActivityCycle_Week 
ActivityCycle_Forever
ActivityCycle_Month*š
ActivityExtraRewardRuleType$
 ActivityExtraRewardRuleType_None -
)ActivityExtraRewardRuleType_Dungeon_Story.
*ActivityExtraRewardRuleType_Dungeon_Normal4
0ActivityExtraRewardRuleType_Dungeon_WeekActivity5
1ActivityExtraRewardRuleType_TaskActivePointReward(
$ActivityExtraRewardRuleType_AsyncPVP,
(ActivityExtraRewardRuleType_Dungeon_Team1
-ActivityExtraRewardRuleType_Dungeon_SemiFixed*™
(ActivityExtraRewardRuleDropConditionType1
-ActivityExtraRewardRuleDropConditionType_None 8
4ActivityExtraRewardRuleDropConditionType_Dungeon_AllA
=ActivityExtraRewardRuleDropConditionType_Dungeon_SingleDouble<
8ActivityExtraRewardRuleDropConditionType_Dungeon_SpecialB
>ActivityExtraRewardRuleDropConditionType_TaskActivePointReward>
:ActivityExtraRewardRuleDropConditionType_AsyncPVP_WinCount:
6ActivityExtraRewardRuleDropConditionType_Dungeon_Range?
;ActivityExtraRewardRuleDropConditionType_Dungeon_OutOfRange*D
Notifications
Notifications_None 
Notifications_PowerFull*¬
	QQVIPFlag
VIP_None 

VIP_NORMAL
VIP_QQ_LEVEL
VIP_BLUE
VIP_RED
	VIP_SUPER

VIP_XINYUE@

VIP_YELLOW€

VIP_ANIMIC€
VIP_XINYUE_NEW€*8
EquipOperationType

OpNone 

Update

Remove*¤
MaasRemindType
MaasRemindType_None !
MaasRemindType_OnceGameRemind#
MaasRemindType_AccumulateRemind$
 MaasRemindType_OnceGameForceRest&
"MaasRemindType_AccumulateForceRest"
MaasRemindType_ForbidGoodNight!
MaasRemindType_ForbidEveryday
MaasRemindType_IsResting2*R
ZkType
ZkType_None 
ZkType_Notice

ZkType_Ban
ZkType_ForceFace*˜
SortType

NoType 	
Actor	
Equip
Item
Aura
CollectionsActor	
Guild

Friend
	MazeActor
RoleNotepad		
Medal
*°
FuncBanType
FuncBanType_None 
FuncBanType_AQ_Login$
 FuncBanType_AQ_AutoFreezeAccount
FuncBanType_AQ_Chat
FuncBanType_AQ_AddFriend
FuncBanType_GrowGuard
FuncBanType_Reported_Chat
FuncBanType_GlobalMail
FuncBanType_SocialPicture
FuncBanType_CareerBan	 
FuncBanType_ChallengeModeBan

FuncBanType_GuildDungeonBan"
FuncBanType_AQ_AppointPlay_PVE$
 FuncBanType_AQ_AppointPlay_Story'
#FuncBanType_AQ_AppointPlay_Activity)
%FuncBanType_AQ_AppointPlay_Tournament#
FuncBanType_AQ_JoinRank_RacePvp<*„

SecLogType
SecLogType_None  
SecLogType_SecRoundStartFlow
SecLogType_SecRoundEndFlow
SecLogType_SecRoundEndCount*X
GachaId
GachaId_None 
GachaId_Normal
GachaId_Periodic
GachaId_Chips*Œ
IDIPErrorCode
IDIPErrorCode_OK  
IDIPErrorCode_PlayerNotExist
IDIPErrorCode_ActorNotExist
IDIPErrorCode_LockPlayer!
IDIPErrorCode_InvalidItemType
IDIPErrorCode_InvalidItemId"
IDIPErrorCode_InvalidItemCount
IDIPErrorCode_NotJoinGuild
IDIPErrorCode_NotMatchGuild
IDIPErrorCode_NoticeURLMiss	
IDIPErrorCode_Notice

IDIPErrorCode_ActiveUser
IDIPErrorCode_InvalidParam!
IDIPErrorCode_MidasExceptioné
IDIPErrorCode_Exceptionê!
IDIPErrorCode_InvalidBanTimeë*’
FightCheckStrategy
FightCheckStrategy_None 
FightCheckStrategy_AllPre
FightCheckStrategy_AllPost 
FightCheckStrategy_RandomPre*Q
ShareActivity

Share_None 
Weekly_ShareÁ»
First_Gold_Actor_ShareÂ»*ı
GameModeType
GameModeType_None 
GameModeType_RacePVP
GameModeType_BurstLinkPvp
GameModeType_TeamPve
GameModeType_AsyncPvp
GameModeType_Tower
GameModeType_WeekActivity
GameModeType_Gold
GameModeType_Challenge*Y
SignboardType
SignboardType_None 
SignboardType_Emote
SignboardType_Erect*Ú
SignboardUnlockType
SignboardUnlockType_None #
SignboardUnlockType_FavourLevel 
SignboardUnlockType_ActorGot2
.SignboardUnlockType_ActorEverGotAndFavourLevel*
&SignboardUnlockType_ActorSignboardGift*Ğ
FavourIncreaseRuleType
FavourIncreaseRuleType_None 
FavourIncreaseRuleType_Gift$
 FavourIncreaseRuleType_Signboard(
$FavourIncreaseRuleType_DeployMission"
FavourIncreaseRuleType_Dungeon
FavourIncreaseRuleType_GM+
'FavourIncreaseRuleType_ActorStarLevelUp'
#FavourIncreaseRuleType_ActorLevelUp#
FavourIncreaseRuleType_ActorGot,
(FavourIncreaseRuleType_ActorSkillLevelUp	-
)FavourIncreaseRuleType_ActorTalentScoreUp
)
%FavourIncreaseRuleType_SetActorAsAura.
*FavourIncreaseRuleType_SetActorAsShowGroup*
&FavourIncreaseRuleType_SetActorAsFavor.
*FavourIncreaseRuleType_SetActorAsHeadPhoto)
%FavourIncreaseRuleType_UseActorAsAura'
#FavourIncreaseRuleType_ActorSkinGot*y
FightOverType
FightOverType_Normal 
FightOverType_Invalid
FightOverType_Overtime
FightOverType_Leave*L

PlayerType
PlayerType_None 
PlayerType_Guest
PlayerType_Sqwx*t
RoleNotepadVoiceType
RVT_NONE 

RVT_COMBAT
	RVT_BOARD
	RVT_EVENT
RVT_SELECTED

RVT_SYSTEM*t
TalentQuality
TalentQuality_Green 
TalentQuality_Blue
TalentQuality_Purple
TalentQuality_Orange*°
MallBuyIntervalType
MallBuyIntervalType_Normal 
MallBuyIntervalType_Day
MallBuyIntervalType_Week
MallBuyIntervalType_Month
MallBuyIntervalType_Forever*d
ResourceRefreshType
ResourceRefresh_None 
ResourceRefresh_Daily
ResourceRefresh_Week*Q
TriggeredEventType
TriggeredEventType_None 
TriggeredEventType_Dungeon*Ü
TempEntranceSwitchType
TempEntranceSwitchType_None 
TempEntranceSwitchType_Task
TempEntranceSwitchType_Mall 
TempEntranceSwitchType_Guild 
TempEntranceSwitchType_Gacha)
%TempEntranceSwitchType_GachaActorChip*
&TempEntranceSwitchType_GachaFriendShip)
%TempEntranceSwitchType_TriggeredEvent+
'TempEntranceSwitchType_ChallengeDungeon'
#TempEntranceSwitchType_EquipDungeon	&
"TempEntranceSwitchType_GoldDungeon
!
TempEntranceSwitchType_Deploy"
TempEntranceSwitchType_RacePvp'
#TempEntranceSwitchType_BurstLinkPvp&
"TempEntranceSwitchType_TeamDungeon'
#TempEntranceSwitchType_EventDungeon 
TempEntranceSwitchType_Tower#
TempEntranceSwitchType_AsyncPvp
TempEntranceSwitchType_Mail'
#TempEntranceSwitchType_GuildDungeon'
#TempEntranceSwitchType_PlatRelationd 
TempEntranceSwitchType_Sharee)
%TempEntranceSwitchType_QQVipPrivilegef0
,TempEntranceSwitchType_QQGameCenterPrivilegeg0
,TempEntranceSwitchType_WXGameCenterPrivilegeh)
%TempEntranceSwitchType_OtherPrivilegei%
!TempEntranceSwitchType_GiftChangej(
$TempEntranceSwitchType_OpenMonthCardk%
!TempEntranceSwitchType_TxRechargel#
TempEntranceSwitchType_KingCardm"
TempEntranceSwitchType_TssOpenn'
#TempEntranceSwitchType_IOSNovelLinko+
'TempEntranceSwitchType_GuildSocialGroupp%
!TempEntranceSwitchType_EightEightq(
$TempEntranceSwitchType_CarnivalMonthr%
!TempEntranceSwitchType_AnimeMovies-
)TempEntranceSwitchType_CollectionActivityt(
$TempEntranceSwitchType_AnimeMovieOctu2
.TempEntranceSwitchType_GuildSocialGroupBackendv(
#TempEntranceSwitchType_IOSPrecommitÈ*]
MazeEventLineType
MazeEventLine_None 
MazeEventLine_Main
MazeEventLine_Branch*e
MazePreConditionType
MazePreCond_None 
MazePreCond_SelectActor
MazePreCond_FightActor*d
MazeEventType
MazeEvent_None 
MazeEvent_Fight
MazeEvent_Buff
MazeEvent_Treasure*}
MazeBuffType
MazeBuff_None 
MazeBuff_AttrChange
MazeBuff_MpChange
MazeBuff_SpChange
MazeBuff_Battle*³
MazeBuffPersistentType
MazeBuffPersistent_None 
MazeBuffPersistent_Cell
MazeBuffPersistent_Battle
MazeBuffPersistent_MapCell 
MazeBuffPersistent_MapBattle*a
MazeBuffRange
MazeBuffRange_None 
MazeBuffRange_FightActor
MazeBuffRange_AllActor*¾

NotifyType
NotityType_None 
NotifyType_Power
NotifyType_Hour12
NotifyType_Hour18
NotifyType_DeployFinish
NotifyType_Hour48NoLogin
NotifyType_Hour24NoLogin*<
ManualActorBox
ManualBox_None 
ManualBox_121002ª±*w
CartoonStoryType
CartoonStoryTypeNone 
Main
Fantasy
EventActivity	
Movie
CartoonTypeCount*„
RewardState
RewardState_None 
RewardState_CanGet
RewardState_HasGot
RewardState_IsDoing
RewardState_WillDo*
CollectionsSceneUnlockType&
"CollectionsSceneUnlockType_Default #
CollectionsSceneUnlockType_Task$
 CollectionsSceneUnlockType_Other*K
ActivityBuffType
ActivityBuffType_Profit 
ActivityBuffType_Fight*¸
GuildChallengeDungeonStatus$
 GuildChallengeDungeonStatus_None %
!GuildChallengeDungeonStatus_Close$
 GuildChallengeDungeonStatus_Open&
"GuildChallengeDungeonStatus_Unopen*5
MinGame
MinGame_None 
MinGame_FlappyBirdùÆbproto3
‚
conditionreward.protooptions.protocommon.proto"U
DBConditionReward
openid (	
reward_qualification (	

reward_got (	"Ú
ApiConditionRewardUploadOpenIdT
result
upload_count (
upload_new_count (
upload_valid_count (:bÒóConditionRewardUploadOpenIdViewÊó/conditionreward/upload-openid/Úóé¢„çº¦å¥–åŠ±åå•ä¸Šä¼ "
ApiConditionReward
manual_box_index (f
get 
has_reward_qualification (
is_reward_got (&
manual_box_reward (2.STResource9
post1
manual_box_res_change (2.STResourceDynamic:<ÒóConditionRewardViewÊó/conditionreward/Úóé¢„çº¦å¥–åŠ±bproto3
°
config.protocommon.proto"R
GameVersionInfo

appVersion (	
patchVersion (
patchChecksum (	"·
GameDynamicConfigEntry

id (
locale (	
versionCheckUrls (	
dirServerUrls (	
notifyServerUrls (	
userAgreementUrls (	
notifyDataServerUrls (	%
httpReconnect	 (2.HttpReconnect)
accountSwitch
 (2.GameAccountSwitch

useHttpDns (
updateCheckUrls (	"‰
HttpReconnect
retryNum (
retryTimeBase (
retryTimeAccumulation (
timeoutBase (
timeoutAccumulation ("=
GameDynamicConfig(
entries (2.GameDynamicConfigEntry"k
GameDebugConfig
fastSkip (
logTags (	
displayAllMessages (
disableWindowLock ("é
GameConstConfig
appId (	
appKey (	
localedConfigUrl (	
gameId (
	channelId (	

tinyClient (

clearPatch (
purchasePlatform	 (
defaultLanguage
 (	
debugc (2.GameDebugConfig"µ
GameSettings
targetFramerate (

resolution (
roleQuality (
shadowQuality	 (
	antiAlias
 (

postEffect (
sceneEffect (
language (	"X
PatchVersionInfoEntry
version (
length (
checksum (
urls (	"L
PatchVersionInfo
version (	'
history (2.PatchVersionInfoEntry"Õ
AccountCache
accountEntry (	
	entryType (	
openId (	
accessToken (	

expireTime (

guestEntry (	
	accountId (	*
socialPlatform (2.MSDKLoginPlatform
localeId	 ("Ú
GameDataCache
	newFriend (

newApplier (5
sortContext (2 .GameDataCache.RecordSortContext
deadlineList (
sortSimpleMode (=
RecordSortContext
key (

id (
reverse ("
iOSBuildSetting
	bundle_id (	
display_name (	
team_id (	
	provision (	
certificate (	
certificate_type (	"(
AndroidBuildSetting
	publisher (	"ˆ
BuildSetting
description (	
publish_target (	
publish_setting (	
platform (	
override_prefix (	
ios (2.iOSBuildSetting%
android (2.AndroidBuildSetting
const (2.GameConstConfig
macros	 (	
language
 (	bproto3

continuouslogin.protooptions.protocommon.proto"–
DBContinuousLogin
	player_id (	
cur_continuous_login (
max_continuous_login (
receive_prize_record (
is_interrupt ("»
ApiContinuousLogin
cur_day (
max_day (b
result
cur_continuous_login (
max_continuous_login (
receive_prize_record (:ÒóApiContinuousLoginViewÊó/ContinuousLogin/Úókè·å–è¿ç»­ç™»å½•ä¿¡æ¯(get),gmä¿®æ”¹å½“å‰ã€æœ€å¤§è¿ç»­ç™»å½•(patch),gmæ¸…ç©ºè¿ç»­ç™»å½•ä¿¡æ¯(delete)"Â
ApiContinuousLoginReceive
days (0
result&

res_change (2.STResourceDynamic:eÒóApiContinuousLoginReceiveViewÊó/ContinuousLogin/ReceivePrize/Úóé¢†å–è¿ç»­ç™»å½•å¥–åŠ±(post)"³
ApiContinuousLoginPrompt
result
is_interrupt (:wÒóApiContinuousLoginPromptViewÊó /ContinuousLogin/InterruptPromptÚó/è·å–è¿ç»­ç™»å½•æ˜¯å¦éœ€è¦ä¸­æ–­æç¤º(get)bproto3
`
cproto.proto"H
CSMessageBody
pbData (	
messageName (	

messageSeq (bproto3
æ/
cs.protocommon.protoerror.protodungeon.protogamedb.proto	api.proto
xlsx.proto"2
Message
type (	
data (
lz4 ("?
RepeatedMessage
	player_id (	
message (2.Message"'

ApiMessage
message (2.Message"ê
RestfulResponse
message (2.Message:
repeated_message (2 .RestfulResponse.RepeatedMessage
raw_json (	+
type (2.RestfulResponse.ResponseType
debug (	
	page_info (2	.PageInfo.
RepeatedMessage
type (	
datas ("d
ResponseType
ResponseType_RawJson 
ResponseType_Message 
ResponseType_RepeatedMessage"O
	CsMessage
seq_cs (
seq_sc_received (
message (2.Message"O
	ScMessage
seq_sc (
seq_cs_received (
message (2.Message"
	CsConnect
token (	

session_id (
seq_cs (
seq_sc_received (
open_id (	
world_id (
device_type ("H
	ScConnect

session_id (
seq_cs_received (
seq_sc ("=
CsHeartbeat
time_ms_client (
time_ms_server ("R
ScHeartbeat
time_ms_server (
time_ms_client (
interval_ms ("4
BasePushMessage
	push_type (
params (	"<
Kick
reason (

ban_reason (	
ban_time ("
KickAll
reason (",
NotifyTempEntranceSwitch
reserved (" 
ClearMessage
senderId (	"&
ServerUpdateNotify
reserved ("Õ
WorldMessage

id (
content (	
time (
senderId (	
sender (	
channel (
subChannelId (
npc_role_id (
guild_id
 (
	local_str (	
local_str_param (	"/
CsWorldChatSubChannel
target_channel ("?
ScWorldChatSubChannel
channel (
total_channel ("?
ScClientError
code (	
is_box (
is_reset ("
CsMTP
data ("é
CsLogin
social_entry (	
device_type (
device (	
channel (
client_version (	
social_platform (
	device_id (	
install_channel (
register_channel	 (
telecom_oper
 (	
network (	
is_game_center (
token (	
	real_imei (	
system_software (	
idfa (	
social_type (	"m
ScLoginBegin
	player_id (	
social_picture (	
utc_offset_seconds (
time_ms_server ("3
ScLoginTutorial 
tutorial_info (2	.Tutorial"İ
ScChapterInfo4
chapter_info (2.ScChapterInfo.st_chapter_info:
chapter_summary (2!.ScChapterInfo.st_chapter_summaryï
st_chapter_info7
record_chapter (2.DbDungeonRecords.ChapterRecordK
record_dungeons (22.ScChapterInfo.st_chapter_info.RecordDungeonsEntryV
RecordDungeonsEntry
key (.
value (2.DbDungeonRecords.DungeonRecord:8ç
st_chapter_summary
current_chapter_id (
unlocked_chapter_id (
finished_chapter_id (
new_chapter_id (
new_dungeon_id (

total_star (
total_normal_star	 (
total_challenge_star
 ("é

ScLoginEnd'
	base_info (2.ScLoginEnd.baseinfo"
base_resource (2.STResource#
channels (2.Misc.ChannelInfoè
baseinfo
level (
name (	
last_login_time (
social_picture (	

head_photo (
fight_medal (
sex (6
home_back_ground (2.DBGamePlayer.HomeBackGround 
friend_apply_level_limit
 ("I
ScMaasRemind
remind_type (
	play_time (
	rest_time (")
CsMaasReportRemind
remind_type ("
ScZk
zk_type (
zk_trace_id (	
zk_title (	
zk_msg (	
zk_begin_time (
zk_end_time (
zk_json_str (	"‘
TeamDungeonRoom
room_id (
room_level_limit (
allow_fast_join (
	master_id (	
member_list (	
dungeon_group (B
member_ready_state (2&.TeamDungeonRoom.MemberReadyStateEntry7
MemberReadyStateEntry
key (	
value (:8"?
CsCreateTeamdungeonRoom
level (
dungeon_group (">
ScCreateTeamdungeonRoom#
	room_info (2.TeamDungeonRoom"M
CsTeamDungeonInvitePlayer
target_id_list (	
invite_from_type (")
CsLeaveTeamDungeonRoom
room_id ("(
ScLeaveTeamDungeonRoom
result ("(
CsJoinTeamDungeonRoom
room_id ("<
ScJoinTeamDungeonRoom#
	room_info (2.TeamDungeonRoom"Ê
ScTeamDungeonMemberChange
	master_id (	
member_list (	L
member_ready_state (20.ScTeamDungeonMemberChange.MemberReadyStateEntry7
MemberReadyStateEntry
key (	
value (:8".
CsTeamDungeonPosChgApply

target_pos ("\
ScTeamDungeonPosChgApply
result (
lock_interval (
applyer_player_id (	"I
CsTeamDungeonPosChgProcess
applyer_player_id (	
is_agree (".
CsTeamDungeonSelectActor

actor_uids ("G
ScTeamDungeonActorChange
	player_id (	
actors (2.STActor",
CsTeamDungeonKickMember
	target_id (	"#
ScTeamDungeonKick
result ("#
CsTeamDungeonReady
ready ("5
CsTeamDungeonChat
content (	
is_fast ("Y
ScTeamDungeonChat
content (	
senderId (	
	send_time (
sender (	"%
CsTeamDungeonStart
reverse ("q
ScTeamDungeonStart
	fight_url (	
dungeon_uid (

dungeon_id (
cost_not_enough_players (	"2
CsTeamDungeonRoomAllowFastJoin
is_allow ("2
ScTeamDungeonRoomAllowFastJoin
is_allow ("P
CsTeamDungeonFastJoin
level (
dungeon_group (
	is_cancel ("E
ScTeamDungeonFastJoin
estimate_interval (
	is_cancel ("0
ScTeamDungeonFastMatchSuccess
reverse (",
CsFastJoinTeamDungeonRoom
reverse (")
CsTeamDungeonAllActors
room_id ("Ò
ScTeamDungeonAllActors:

actor_info (2&.ScTeamDungeonAllActors.ActorInfoEntry&

actorgroup
actors (2.STActorT
ActorInfoEntry
key (	1
value (2".ScTeamDungeonAllActors.actorgroup:8"R
ScTempEntranceSwitchInfo6
switch_info (2!.DbTempEntranceSwitch.Switch_Info"¡
ScServerUpdateTimeInfo8
	time_info (2%.ScServerUpdateTimeInfo.TimeInfoEntryM
TimeInfoEntry
key (+
value (2.DbServerUpdateInfo.TimeInfo:8"
ScTSSAntiData
data (bproto3
Ü
deploy.protooptions.protocommon.proto
xlsx.protofight.protodungeon.protogamedb.protoxlsx_server.prototriggered_event.proto"
ApiDeployMissionN
get/
mission_info (2.DBGamePlayer.MissionInfo
accomplish_num (:9ÒóDeployMissionViewÊó/deploy/mission/Úóæ´¾é£ä»»åŠ¡"è
ApiDeployMissionAttend

id (
	actor_uid (\
post
attend_result (
is_play_story (&

res_change (2.STResourceDynamic:QÒóDeployMissionAttendViewÊó/deploy/deploymissionattend/Úóå‚ä¸æ´¾é£ä»»åŠ¡"ı
ApiDeployMissionReward

id (ƒ
result
reward_result (&

res_change (2.STResourceDynamicB
favour_change (2+.ApiDeployMissionReward.result.FavourChange/

event_info (2.DBTriggeredEvent.EventInfoE
FavourChange
role_id (
favour (
favour_level (:QÒóDeployMissionRewardViewÊó/deploy/deploymissionreward/Úóæ´¾é£ä»»åŠ¡å¥–åŠ±"ß
ApiDeployMissionSpeedUp

id (
cost (2.STResourceH
result
speedup_result (&

res_change (2.STResourceDynamic:SÒóDeployMissionSpeedUpViewÊó/deploy/deploymissionspeedup/Úóæ´¾é£ä»»åŠ¡åŠ é€Ÿ"Ä
ApiDeployMissionDiscard

id (H
result
discard_result (&

res_change (2.STResourceDynamic:SÒóDeployMissionDiscardViewÊó/deploy/deploymissiondiscard/Úóæ´¾é£ä»»åŠ¡æ”¾å¼ƒ"’
ApiDeployMissionSummary
get

accomplish (:\ÒóDeployMissionSummaryViewÊó/deploy/deploymissionsummary/Úóæ´¾é£æ˜¯å¦æœ‰ä»»åŠ¡å®Œæˆbproto3
†/
 google/protobuf/descriptor.protogoogle.protobuf"G
FileDescriptorSet2
file (2$.google.protobuf.FileDescriptorProto"Û
FileDescriptorProto
name (	
package (	

dependency (	
public_dependency
 (
weak_dependency (6
message_type (2 .google.protobuf.DescriptorProto7
	enum_type (2$.google.protobuf.EnumDescriptorProto8
service (2'.google.protobuf.ServiceDescriptorProto8
	extension (2%.google.protobuf.FieldDescriptorProto-
options (2.google.protobuf.FileOptions9
source_code_info	 (2.google.protobuf.SourceCodeInfo
syntax (	"©
DescriptorProto
name (	4
field (2%.google.protobuf.FieldDescriptorProto8
	extension (2%.google.protobuf.FieldDescriptorProto5
nested_type (2 .google.protobuf.DescriptorProto7
	enum_type (2$.google.protobuf.EnumDescriptorProtoH
extension_range (2/.google.protobuf.DescriptorProto.ExtensionRange9

oneof_decl (2%.google.protobuf.OneofDescriptorProto0
options (2.google.protobuf.MessageOptionsF
reserved_range	 (2..google.protobuf.DescriptorProto.ReservedRange
reserved_name
 (	e
ExtensionRange
start (
end (7
options (2&.google.protobuf.ExtensionRangeOptions+
ReservedRange
start (
end ("g
ExtensionRangeOptionsC
uninterpreted_optionç (2$.google.protobuf.UninterpretedOption*	è€€€€"¼
FieldDescriptorProto
name (	
number (:
label (2+.google.protobuf.FieldDescriptorProto.Label8
type (2*.google.protobuf.FieldDescriptorProto.Type
	type_name (	
extendee (	
default_value (	
oneof_index	 (
	json_name
 (	.
options (2.google.protobuf.FieldOptions"¶
Type
TYPE_DOUBLE

TYPE_FLOAT

TYPE_INT64
TYPE_UINT64

TYPE_INT32
TYPE_FIXED64
TYPE_FIXED32
	TYPE_BOOL
TYPE_STRING	

TYPE_GROUP

TYPE_MESSAGE

TYPE_BYTES
TYPE_UINT32
	TYPE_ENUM
TYPE_SFIXED32
TYPE_SFIXED64
TYPE_SINT32
TYPE_SINT64"C
Label
LABEL_OPTIONAL
LABEL_REQUIRED
LABEL_REPEATED"T
OneofDescriptorProto
name (	.
options (2.google.protobuf.OneofOptions"¤
EnumDescriptorProto
name (	8
value (2).google.protobuf.EnumValueDescriptorProto-
options (2.google.protobuf.EnumOptionsN
reserved_range (26.google.protobuf.EnumDescriptorProto.EnumReservedRange
reserved_name (	/
EnumReservedRange
start (
end ("l
EnumValueDescriptorProto
name (	
number (2
options (2!.google.protobuf.EnumValueOptions"
ServiceDescriptorProto
name (	6
method (2&.google.protobuf.MethodDescriptorProto0
options (2.google.protobuf.ServiceOptions"Á
MethodDescriptorProto
name (	

input_type (	
output_type (	/
options (2.google.protobuf.MethodOptions
client_streaming (:false
server_streaming (:false"¦
FileOptions
java_package (	
java_outer_classname (	"
java_multiple_files
 (:false)
java_generate_equals_and_hash (B%
java_string_check_utf8 (:falseF
optimize_for	 (2).google.protobuf.FileOptions.OptimizeMode:SPEED

go_package (	"
cc_generic_services (:false$
java_generic_services (:false"
py_generic_services (:false#
php_generic_services* (:false

deprecated (:false
cc_enable_arenas (:false
objc_class_prefix$ (	
csharp_namespace% (	
swift_prefix' (	
php_class_prefix( (	
php_namespace) (	
php_metadata_namespace, (	
ruby_package- (	C
uninterpreted_optionç (2$.google.protobuf.UninterpretedOption":
OptimizeMode	
SPEED
	CODE_SIZE
LITE_RUNTIME*	è€€€€J&'"ò
MessageOptions&
message_set_wire_format (:false.
no_standard_descriptor_accessor (:false

deprecated (:false
	map_entry (C
uninterpreted_optionç (2$.google.protobuf.UninterpretedOption*	è€€€€J	J	
"
FieldOptions:
ctype (2#.google.protobuf.FieldOptions.CType:STRING
packed (?
jstype (2$.google.protobuf.FieldOptions.JSType:	JS_NORMAL
lazy (:false

deprecated (:false
weak
 (:falseC
uninterpreted_optionç (2$.google.protobuf.UninterpretedOption"/
CType

STRING 
CORD
STRING_PIECE"5
JSType
	JS_NORMAL 
	JS_STRING
	JS_NUMBER*	è€€€€J"^
OneofOptionsC
uninterpreted_optionç (2$.google.protobuf.UninterpretedOption*	è€€€€"“
EnumOptions
allow_alias (

deprecated (:falseC
uninterpreted_optionç (2$.google.protobuf.UninterpretedOption*	è€€€€J"}
EnumValueOptions

deprecated (:falseC
uninterpreted_optionç (2$.google.protobuf.UninterpretedOption*	è€€€€"{
ServiceOptions

deprecated! (:falseC
uninterpreted_optionç (2$.google.protobuf.UninterpretedOption*	è€€€€"­
MethodOptions

deprecated! (:false_
idempotency_level" (2/.google.protobuf.MethodOptions.IdempotencyLevel:IDEMPOTENCY_UNKNOWNC
uninterpreted_optionç (2$.google.protobuf.UninterpretedOption"P
IdempotencyLevel
IDEMPOTENCY_UNKNOWN 
NO_SIDE_EFFECTS

IDEMPOTENT*	è€€€€"
UninterpretedOption;
name (2-.google.protobuf.UninterpretedOption.NamePart
identifier_value (	
positive_int_value (
negative_int_value (
double_value (
string_value (
aggregate_value (	3
NamePart
	name_part (	
is_extension ("Õ
SourceCodeInfo:
location (2(.google.protobuf.SourceCodeInfo.Location†
Location
path (B
span (B
leading_comments (	
trailing_comments (	!
leading_detached_comments (	"§
GeneratedCodeInfoA

annotation (2-.google.protobuf.GeneratedCodeInfo.AnnotationO

Annotation
path (B
source_file (	
begin (
end (B
com.google.protobufBDescriptorProtosHZ>github.com/golang/protobuf/protoc-gen-go/descriptor;descriptorø¢GPBªGoogle.Protobuf.Reflection
½
	dir.protooptions.protocommon.proto"T
PlayerBaseInfo
player_level (
player_name (	
last_login_time ("M
	DBIpLimit
ip_list (2.DBIpLimit.ip 
ip
begin (	
end (	"¥
	WorldInfo
world_id (

world_name (	
account_url (	
account_web_url (	
push_url (	
fci_url (	
gm_url (	
	match_url (	"*
DirWorld

world_info (2
.WorldInfo".
DirStat
stat (
dir_open_time ("'
CommitVersion
commit_version (	"q
CommitVersionConfig*
ios_commit_version (2.CommitVersion.
android_commit_version (2.CommitVersion">
FormalVersion
base_version (	
suggest_version (	"‹
FormalVersionConfig
is_check_version (*
ios_formal_version (2.FormalVersion.
android_formal_version (2.FormalVersion"…
WorldInfoConfig%
formal_world_info (2
.WorldInfo%
commit_world_info (2
.WorldInfo$
guest_world_info (2
.WorldInfo"§
AccountSwitch1
formal_account_switch (2.GameAccountSwitch1
commit_account_switch (2.GameAccountSwitch0
guest_account_switch (2.GameAccountSwitch"ç
DBDirConfig
dir_name (	
dir_stat (2.DirStat&
world_config (2.WorldInfoConfig,
commit_version (2.CommitVersionConfig,
formal_version (2.FormalVersionConfig&
account_switch (2.AccountSwitch"±
ReqDir
client_version (	
device_type ( 
player_type (2.PlayerTypeB
result
dir_stat (2.DirStat
	dir_world (2	.DirWorld:ÒóDirViewÊó/Dir/"¦
ReqWorldConfig+
world_config_save (2.WorldInfoConfigA
result
ret (*
world_config_get (2.WorldInfoConfig:$ÒóWorldConfigViewÊó/WorldConfig/"‚

ReqDirStat
dir_stat_save (2.DirStat5
result
ret (
dir_stat_get (2.DirStat:ÒóDirStatViewÊó	/DirStat/"¸
ReqCommitVersion1
commit_version_save (2.CommitVersionConfigG
result
ret (0
commit_version_get (2.CommitVersionConfig:(ÒóCommitVersionViewÊó/CommitVersion/"¸
ReqFormalVersion1
formal_version_save (2.FormalVersionConfigG
result
ret (0
formal_version_get (2.FormalVersionConfig:(ÒóFormalVersionViewÊó/FormalVersion/"…
ReqAllConfig1
formal_version_save (2.FormalVersionConfig1
commit_version_save (2.CommitVersionConfig+
world_config_save (2.WorldInfoConfig
dir_stat_save (2.DirStat+
account_switch_save (2.AccountSwitchñ
result
ret
 (0
formal_version_get (2.FormalVersionConfig0
commit_version_get (2.CommitVersionConfig*
world_config_get (2.WorldInfoConfig
dir_stat_get (2.DirStat*
account_switch_get (2.AccountSwitch: ÒóAllConfigViewÊó/AllConfig/"˜

ApiIpLimit
ip_list (2.ApiIpLimit.ip 
ip
begin (	
end (	)
result
ip_list (2.ApiIpLimit.ip:ÒóIpLimitViewÊó	/iplimit/"à
ReqDirAccount
client_version (	
device_type (~
result
dir_stat (2.DirStat
account_url (	
account_web_url (	*
account_switch (2.GameAccountSwitch:"ÒóDirAccountViewÊó/DirAccount/bproto3
÷P
dungeon.protooptions.protocommon.protogamedb.proto
xlsx.protofight.prototower.prototriggered_event.proto
maze.proto"&
DbBattleRoomGlobal
room_uid ("¿
DbTeamDungeonWeekIdsO
activity_dungeon_group (2/.DbTeamDungeonWeekIds.ActivityDungeonGroupEntry
last_refresh_time (;
ActivityDungeonGroupEntry
key (
value (:8"Ù
DbTeamDungeonInfo
dungeon_uid (

dungeon_id (
dungeon_type (;
player_actors (2$.DbTeamDungeonInfo.PlayerActorsEntry
expire_seconds (

player_pos (	
level_limit (
state (%
	actorinfo
actors (2.STActorQ
PlayerActorsEntry
key (	+
value (2.DbTeamDungeonInfo.actorinfo:8"Ø
DbTeamDungeonBalanceRecord
	player_id (	7
record (2'.DbTeamDungeonBalanceRecord.RecordEntry2
battle_info
damages (

actor_uids (ß
battle_record

dungeon_id (N
player_info (29.DbTeamDungeonBalanceRecord.battle_record.PlayerInfoEntry
is_win (Z
PlayerInfoEntry
key (	6
value (2'.DbTeamDungeonBalanceRecord.battle_info:8X
RecordEntry
key (8
value (2).DbTeamDungeonBalanceRecord.battle_record:8"‘
DbBattleRoom
room_uid (
name (	
expire_seconds (
create_time (
creator (	%
members (2.DbBattleRoom.Member
instance_started (

dungeon_id (N
Member
	player_id (	
actor_group_uid (
actors (2.STActor"³
DbBattleInstance
room_uid (
expire_seconds (
base (2.FightBaseInfo 
active (2.FightActiveData*
balance (2.DbBattleInstance.Balance	
Balance"

DbActivityDungeonRecords
	player_id (	7
records (2&.DbActivityDungeonRecords.RecordsEntry>
star_reward (2).DbActivityDungeonRecords.StarRewardEntry­
DungeonRecord

dungeon_id (
unlocked (
finished (
targets_done (
multi_power	 (

done_times
 (
finish_condition (
new (Æ
ActivityRecords\
activityid_records (2@.DbActivityDungeonRecords.ActivityRecords.ActivityidRecordsEntryİ
ActivityIdRecordsh
dungeon_records (2O.DbActivityDungeonRecords.ActivityRecords.ActivityIdRecords.DungeonRecordsEntry^
DungeonRecordsEntry
key (6
value (2'.DbActivityDungeonRecords.DungeonRecord:8u
ActivityidRecordsEntry
key (J
value (2;.DbActivityDungeonRecords.ActivityRecords.ActivityIdRecords:8¯
ActivityStarReward]
activityid_reward (2B.DbActivityDungeonRecords.ActivityStarReward.ActivityidRewardEntry>
ActivityIdStarReward

reward_got (

total_star (z
ActivityidRewardEntry
key (P
value (2A.DbActivityDungeonRecords.ActivityStarReward.ActivityIdStarReward:8Y
RecordsEntry
key (8
value (2).DbActivityDungeonRecords.ActivityRecords:8_
StarRewardEntry
key (;
value (2,.DbActivityDungeonRecords.ActivityStarReward:8"õ
DbEventDungeonReward
	player_id (	@
reward_records (2(.DbEventDungeonReward.RewardRecordsEntry(
EventDungeonReward

reward_got (^
RewardRecordsEntry
key (7
value (2(.DbEventDungeonReward.EventDungeonReward:8"ä
	DbDungeon
dungeon_uid (
	player_id (	
expire_seconds (
multi_power (
client_version (	
device_type (&
	base_info (2.DbDungeon.BaseInfo%
active_info (2.FightActiveData,
balance_info (2.DbDungeon.BalanceInfo,

tower_info (2.DbDungeon.TowerInitData3

guild_info (2.DbDungeon.GuildDungeonInitData
BaseInfo

dungeon_id (
	actor_uid (
create_time (
	quit_time (
activity_type (
activity_id (
tower_floor (
actor_id (
aura_player_id	 (	
aura_actor_id
 (
aura_stranger (
aura_actor_quality (
dungeon_type (
actors (2.STActor
	event_key (	
medalId (8
reduce_info (2#.DbDungeon.BaseInfo.ReduceInfoEntry
is_challenge_mode (
fight_buffs (
npc_cnt (1
ReduceInfoEntry
key (	
value (:8±
BalanceInfo
balanced (
balance_time (
targets_done (&

res_change (2.STResourceDynamic4

exp_change (2 .DbDungeon.BalanceInfo.ExpChange:
favour_change (2#.DbDungeon.BalanceInfo.FavourChange
player_level (#
player_level_before_balance	 (
win
 (
first_finish (/
profits (2.DBGamePlayer.ProfitDetailInfo
lose_num (/

event_info (2.DBTriggeredEvent.EventInfo&

exp_return (2.STResourceDynamico
	ExpChange
	actor_uid (
exp (
exp_new (
level (
	level_new (
add_exp (_
FavourChange
role_id (
favour (
favour_level (
old_favour_level (Z
TowerInitData
myHpList (
enemyHpList (
energySp (
energyMp (R
GuildDungeonInitData
enemyHpList (
guild_id (
rent_actors ("Û
DbSemiFixedDungeonInfo
	player_id (	3
groups (2#.DbSemiFixedDungeonInfo.GroupsEntryš
SemiFixedDungeonGroupE
members (24.DbSemiFixedDungeonInfo.SemiFixedDungeonGroup.Member:
Member
index (
is_npc (
	actor_uid (\
GroupsEntry
key (<
value (2-.DbSemiFixedDungeonInfo.SemiFixedDungeonGroup:8"‰
ApiChapterSummary#
get
get (2.ChapterSummary:OÒóChapterSummaryViewÊó/dungeon/chapter-summary/Úóå¾—åˆ°æ‰€æœ‰ç« èŠ‚è®°å½•"Ï
ApiChapterReward
star_reward (I
result
star_reward_got (&

res_change (2.STResourceDynamic:[ÒóApiChapterRewardViewÊó)/dungeon/chapter/<int:chapter_id>/reward/Úóè·å–ç« èŠ‚å¥–åŠ±"›
ChapterSummary
current_chapter_id (
unlocked_chapter_id (
finished_chapter_id (
new_chapter_id (
new_dungeon_id (

total_star (
total_normal_star	 (
total_challenge_star
 (#
unlocked_story_dungeons_cnt (#
finished_story_dungeons_cnt ($
unlocked_normal_dungeons_cnt ($
finished_normal_dungeons_cnt (&
unlocked_training_dungeons_cnt (&
finished_training_dungeons_cnt ('
unlocked_challenge_dungeons_cnt ('
finished_challenge_dungeons_cnt ("½
ApiChapterInfoM
result 
summary (2.ChapterSummary!
record (2.DbDungeonRecordsØ
get7
record_chapter (2.DbDungeonRecords.ChapterRecord@
record_dungeons (2'.ApiChapterInfo.get.RecordDungeonsEntryV
RecordDungeonsEntry
key (.
value (2.DbDungeonRecords.DungeonRecord:8:€ÒóChapterInfoViewÊó4/dungeon/chapter/<int:chapter_id>/,/dungeon/chapter/Úó1å¾—åˆ°ç« èŠ‚è¯¦ç»†ä¿¡æ¯,åŒ…æ‹¬æ‰€æœ‰å‰¯æœ¬è®°å½•"
ApiChapterRecord
new (9
result/
result (2.DbDungeonRecords.ChapterRecord:BÒóChapterRecordViewÊó)/dungeon/chapter-record/<int:chapter_id>/"÷
ApiActorGroup:
members (2).DBGamePlayer.ActorGroupInfo.Group.Member5
actor_groups (2.ApiActorGroup.ActorGroupsEntryV
ActorGroupsEntry
key (1
value (2".DBGamePlayer.ActorGroupInfo.Group:8<
result2
result (2".DBGamePlayer.ActorGroupInfo.Group:]ÒóActorGroupViewÊó./ActorGroup/,/ActorGroup/<int:actor_group_id>/Úóå¾—åˆ°å•ä¸ªè§’è‰²ç»„"’

ApiDungeon
select_actor_group_id (

dungeon_id (
aura_player_id (	
aura_actor_id (
aura_stranger (
aura_actor_quality (
multi_power (
	event_key (	0
reduce_info	 (2.ApiDungeon.ReduceInfoEntry
is_challenge_mode
 (1
ReduceInfoEntry
key (	
value (:8m
result
dungeon_uid (&
	base_info (2.DbDungeon.BaseInfo&

res_change (2.STResourceDynamic:XÒóDungeonViewÊó%/dungeon/,/dungeon/<int:dungeon_uid>/Úóå‰¯æœ¬è¿›å…¥ï¼Œé€€å‡º,æŸ¥è¯¢"®
ApiActivityDungeon
select_actor_group_id (

dungeon_id (
activity_type (
activity_id (
multi_power (
aura_player_id (	
aura_actor_id (
aura_stranger (
aura_actor_quality	 (E
result
dungeon_uid (&
	base_info (2.DbDungeon.BaseInfo:{ÒóApiActivityDungeonViewÊó7/activity-dungeon/,/activity-dungeon/<int:dungeon_uid>/Úó"æ´»åŠ¨å‰¯æœ¬è¿›å…¥ï¼Œé€€å‡º,æŸ¥è¯¢"ö
ApiEventDungeonStarReward
activity_id (
star (ù
getI
reward_summary (21.ApiEventDungeonStarReward.get.RewardSummaryEntry=
reward_summary_info

reward_got (

total_star (h
RewardSummaryEntry
key (A
value (22.ApiEventDungeonStarReward.get.reward_summary_info:80
result&

res_change (2.STResourceDynamic:‡ÒóApiEventDungeonStarRewardViewÊó/event-dungeon/star-rewardÚóDposté¢†å–å‘¨å›å‰¯æœ¬æ˜Ÿçº§å¥–åŠ±,getæŸ¥è¯¢å‘¨å›å‰¯æœ¬å¥–åŠ±ä¿¡æ¯"Ã
ApiEventDungeon
select_actor_group_id (

dungeon_id (
activity_type (
activity_id (
multi_power (
aura_player_id (	
aura_actor_id (
aura_stranger (
aura_actor_quality	 (m
result
dungeon_uid (&
	base_info (2.DbDungeon.BaseInfo&

res_change (2.STResourceDynamic:kÒóApiEventDungeonViewÊó1/event-dungeon/,/event-dungeon/<int:dungeon_uid>/Úóå‘¨å›å‰¯æœ¬è¿›å…¥ï¼Œé€€å‡º"­
ApiEventDungeonBalance0
result&
result (2.DbDungeon.BalanceInfo:aÒóApiEventDungeonBalanceViewÊó)/event-dungeon/<int:dungeon_uid>/balance/Úóå‰¯æœ¬ç»“ç®—ä¿¡æ¯"É
ApiGoldDungeon
select_actor_group_id (
aura_player_id (	
aura_actor_id (
aura_stranger (
aura_actor_quality	 (E
result
dungeon_uid (&
	base_info (2.DbDungeon.BaseInfo:oÒóApiGoldDungeonViewÊó//gold-dungeon/,/gold-dungeon/<int:dungeon_uid>/Úó"é‡‘å¸å‰¯æœ¬è¿›å…¥ï¼Œé€€å‡º,æŸ¥è¯¢"”
ApiGoldDungeonRecord)
result
last_gold_dungeon_level (:QÒóApiGoldDungeonRecordViewÊó/gold-dungeon-record/Úóå¾—åˆ°é‡‘å¸å‰¯æœ¬è¿›åº¦"°
ApiGoldDungeonBalance0
result&
result (2.DbDungeon.BalanceInfo:eÒóApiGoldDungeonBalanceViewÊó(/gold-dungeon/<int:dungeon_uid>/balance/Úóé‡‘å¸å‰¯æœ¬ç»“ç®—ä¿¡æ¯"—
ApiRacePVPDungeon
select_actor_group_id (

dungeon_id (
aura_player_id (	
aura_actor_id (
aura_stranger (
aura_actor_quality (m
result
dungeon_uid (&
	base_info (2.DbDungeon.BaseInfo&

res_change (2.STResourceDynamic:~ÒóApiRacePVPDungeonViewÊó5/racepvp-dungeon/,/racepvp-dungeon/<int:dungeon_uid>/Úó(æ®‹æ¢¦ä¹‹å¢ƒå‰¯æœ¬è¿›å…¥ï¼Œé€€å‡º,æŸ¥è¯¢
ç
google/protobuf/duration.protogoogle.protobuf"*
Duration
seconds (
nanos (B|
com.google.protobufBDurationProtoPZ*github.com/golang/protobuf/ptypes/durationø¢GPBªGoogle.Protobuf.WellKnownTypesbprotIç(Û
·
google/protobuf/empty.protogoogle.protobuf"
EmptyBv
com.google.protobufB
EmptyProtoPZ'github.com/golang/protobuf/ptypes/emptyø¢GPBªGoogle.Protobuf.WellKnownTypesbprojÇ"
ın
error.protooptions.proto*Ön
ErrorMessageBox
Error_default 
Error_EntryHasBeenUsed
Error_YouMustInputIdChina
Error_YouMustInputRealName
Error_EntryCantBeSameBefore"
Error_YouMustInputExistAccount
Error_AccountInvalid&
"Error_AccountHasBeenUsedForThisApp
Error_VerifyCodeError
Error_EntryForbidden	
Error_PasswordDoNotMatch

Error_NotRegisterForThisApp!
Error_PageViewCntPerPageError
Error_ParsePbError
Error_PlayerIdError
Error_PlayerNotFound
Error_TargetPlayerNotFound
Error_NameHasBeenUsed
Error_RequestFailed
Error_TokenVerifyFailed
Error_RestableError
Error_PlayerLevelNotReach
Error_PlayerCreateFailed"
Error_NameCannotStartWithSharp&
"Error_NameChangeCannotSameAsBefore
Error_ActorGroupError
Error_ActorNotFound
Error_ActorCreateFailed
Error_CostResourceNotEnough 
Error_ActorStarLevelupFailed
Error_ActorLevelupNotReach
Error_ActorIsLock "
Error_ActorReplaceTalentFailed!
Error_ActorSkillLearnFailed"
Error_ActorLevelupFailed#
Error_EquipNotFound%
Error_EquipCreateFailed&
Error_EquipOnFailed'
Error_EquipOffFailed(
Error_EquipEnhanceFailed)
Error_EquipDecomposeFailed*
Error_GachaTradeFailed+
Error_ItemNotFound,
Error_ItemCreateFailed-
Error_ActivityTypeError.
Error_ActivityNotStart/$
 Error_ActivityDungeonNotActivity0
Error_AchievementError1!
Error_AchievementRewardFailed2
Error_TradeCodeGenFailed3
Error_TradeCodeNotExist4
Error_PlayerIdNotMatch5
Error_PurchaseVerifyFailed6
Error_PurchaseFinishFailed7
Error_ShopGetGoodsFailed8 
Error_ShopRefreshGoodsFailed9
Error_ShopTradeFailed:
Error_TodaySignedAlready;
Error_SignedDaysNotReach<
Error_SignedRewardBeenGot=
Error_TaskNotFound>
Error_TaskRewardGetFailed?
Error_ChatSpeakTooFast@
Error_ChatChannelErrorA
Error_ChatPrivateToSelfB!
Error_TargetPlayerIsNotOnlineC
Error_HaveNoGuildD
Error_WorldSubChannelIsFullE
Error_CommentErrorF
Error_ContentIsEmptyG
Error_YouHaveLikeItBeforeH
Error_GuildNotFoundI
Error_GuildCreateFailedJ
Error_GuildDismissFailedK
Error_ContentOrNameIsDirtyL
Error_GuildHaveNoPermissionM!
Error_GuildYouAreNotPresidentN
Error_TargetIsNotInGuildO
Error_GuildJoinFailedP#
Error_GuildPresidentCannotLeaveQ 
Error_GuildRequestJoinFailedR!
Error_GuildInvitePlayerFailedS
Error_GuildNotDonateT
Error_GuildDonateBeenGotU
Error_OtherOneDonateActorV
Error_DonateActorFailedW
Error_NotGuildMemberX
Error_YouHaveVotedGuildY
Error_MailNotFoundZ
Error_MailInvalid[ 
Error_MailAttachMentGetFaild\
Error_RelationApplySelf]#
Error_RelationBeenFriendAlready^
Error_HandleApplyListFailed_
Error_RelationPointIsMax`
Error_NoRelationPointToGeta
Error_TowerRewardFailedb
Error_WipeFloorFailedc
Error_TowerRewardBeenGotd"
Error_TowerRewardFloorNotReache
Error_DungeonNotFoundf
Error_DungeonBelongErrorg
Error_DungeonCreateFailedh
Error_DungeonQuitFailedi
Error_DungeonBalanceFailedj
Error_DungeonRoomNotFoundk!
Error_DungeonRoomCreateFailedl
Error_DungeonRoomJoinFailedm 
Error_DungeonRoomLeaveFailedn
Error_NotInBattleo
Error_TournamentTimesErrorp
Error_TournamentNotFoundq 
Error_TournamentCreateFailedr!
Error_TournamentBalanceFaileds
Error_TournamentQuitFailedt 
Error_TournamentRewardFailedu 
Error_YouHaveLeaveFromServerv
Error_RewardBeenGotw
Error_ChapterStarNotEnoughx 
Error_ResBuyPriceCheckFailedy
Error_ResBuyPriceBuyFailedz 
Error_AsyncPvpGroupSetFailed{
Error_ResBuyPriceNoRes|
Error_TowerHasRefreshed}
Error_AsyncPvpMatchFailed~
Error_TowerFloorNotEnough
Error_ActorGroupEmpty€
Error_MustFinishPreDun
Error_DungeonOnlyOnce‚ 
Error_ActorInheritingFailedƒ
Error_TaskExpired„
Error_AchievementNotDone…
Error_GachaTradeMax‡
Error_TooManyActorChipˆ
Error_DbErrorŒ
Error_RoleNotFound
Error_DungeonStarNotEnough
Error_FunctionNotOpen
Error_TowerGroupLocked
Error_PresentNotFound‘
Error_AccountNotYoursÈ
Error_AccountNotExistÉ"
Error_AccountVerifyCodeFailedÊ%
 Error_AccountVerifyCodeNewFailedË
Error_AccountCaptchaFailedÌ
Error_AccountEntryNotExistÍ$
Error_AccountEntryTypeDifferentÎ
Error_AccountAppIdNotExistÏ
Error_NotForTheGameĞ'
"Error_AccountEntryTypeNotSupportedÑ
Error_ParamInvalidÒ
Error_MustBeGmÓ
Error_MustBeSafeÔ
Error_StatusErrorÕ
Error_ClientVersionErrorÖ"
Error_ClientTerminalTypeError×"
Error_VerifyClientTokenFailedØ 
Error_SocialTypeUnsupportedÙ
Error_MsdkTokenFailedÚ
Error_SocialTokenFailedÛ
Error_AppkeyErrorÜ
Error_UserIdNotAllowedİ
Error_LdapCheckFailedŞ
Error_Closedß
Error_ConfigInternalErrorà
Error_OpenIdHasBeenExistedá
Error_PlatformNotSupportedâ
Error_AccountForbiddenã
Error_EntryAlreadyExistä
Error_EntryTypeDifferentå
Error_TooManyEntryæ
Error_NotSupportAppIdç
Error_CreatedBeforeè
Error_TextTooLongé$
Error_DeployMissionNotEnoughResê
Error_DeployMissionNoë
Error_MessageNotFoundì
Error_MidasFailedí
Error_OnlyAllowdYourselfî
Error_OnlyAllowdYourFriendï"
Error_DeployMissionStatusNoneğ!
Error_DeployMissionStatusEndñ
Error_VersionNotFoundò 
Error_ResourceComposeFailedó
Error_DeployMissionCloseô!
Error_TalentResetNoEnoughResõ
Error_TalentResetNoActorö
Error_TalentResetPosErr÷
Error_LoadingResø
Error_ResNotFoundù
Error_ResNotFoundInVersionú
Error_ResNotFoundTutorialû
Error_ResNotFoundTalentü
Error_NotFoundCDKEYı
Error_CDKEYNotActivationş!
Error_YouMustInputExistCDKeyÿ
Error_ResBuyAttendTimes€
Error_ResBuyPriceErr!
Error_AllWorldSubChannelFull‚
Error_ActorCommentReachMaxƒ
Error_TaskNotFinish„
Error_BookingIsFull…
Error_BanChat†
Error_MedalNotExist‡
Error_ExpiredRelationDataˆ
Error_AuraStillInCD‰
Error_PlayerPowerFullŠ
Error_MailNotExist‹
Error_NoCollectionThisRoleŒ"
Error_NoEnoughCollectionActor
Error_NotSupportMode
Error_NoThisNovel
Error_NoCollectionNovel
Error_NoThisNovelReward‘
Error_NovelCoverHaveWear’
Error_NoThisNovelCover“
Error_ShopHide”
Error_NoEnoughNovelScore•
Error_NoveMaxRewardNum–*
%Error_GuildOnlyPresidentCanKickMember—'
"Error_GuildVicePresidentCountLimit˜#
Error_GiftCodeTypeNotAvailable™
Error_GiftCodeNotExistš
Error_GiftCodeNotOnTime›#
Error_GiftCodePlatformNotMatchœ"
Error_GiftCodeTypeUseReachMax
Error_GiftCodeHasBeenUsed#
Error_MailAttachmentLevelLimitŸ!
Error_GuildSkillOpenedBefore 
Error_OnlyAllowdGuildAdmin¡
Error_ChapterNotComplete¢
Error_StarNotComplete£
Error_GuildVoteExpired¤
Error_GuildNotDonateItem¥
Error_GuildDonateCountMax¦!
Error_GuildDonateItemNotSame§ 
Error_GuildNotDonateRequest¨
Error_GuildTouchTimeLimit©
Error_GachaShopInvalidGoodª
Error_BuyCountLimit«
Error_SealFilled¬
Error_SealRewarded­
Error_SealRewardNotFinish®#
Error_SealRewardTypeNotSupport¯
Error_PlayerInOtherGuild°
Error_PlayerNotRequested±
Error_PlayerNotInvited²!
Error_BurstLinkPvpCreateFail³
Error_BurstLinkPvpNo´"
Error_BurstLinkPvpNoThisActorµ!
Error_BurstLinkPvpHaveAttend¶ 
Error_BurstLinkPvpRandActor·
Error_BurstLinkPvpRandStep¸'
"Error_BurstLinkPvpInvalidRandActor¹#
Error_BurstLinkPvpInvalidGroupº$
Error_BurstLinkPvpBalanceFailed»
Error_BurstLinkClose¼
Error_BurstLinkMatch½
Error_RacePVPNoRoom¾ 
Error_RacePVPDungeonNotOpen¿
Error_PVPRandStepÀ
Error_RacePVPSegRewardÁ
Error_BurstLinkNotStartÃ
Error_NotEnoughGoldTicketÅ 
Error_BurstLinkPvpDunFailedÆ!
Error_VoteRoleConfigNotExistÇ
Error_VoteRoleDataNotExistÈ 
Error_VoteRoleRoleIdInvalidÉ!
Error_VoteRoleRecordNotExistÊ 
Error_VoteRoleRecordInvalidË
Error_RacePvpNoBattleÌ)
$Error_NameChangeCannotStartWithGuestÍ
Error_GuildMemberMaxÎ
Error_DeployMissionMaxNumÏ
Error_ServerTooBusyĞ$
Error_GuildLeaveTimeNotCooldownÑ*
%Error_GuildTargetLeaveTimeNotCooldownÒ
Error_GuildTargetIsInGuildÓ
Error_ChatTextTooLongÔ
Error_GuildNameTooLongÕ
Error_DungeonDoneTimesÖ 
Error_NoBuyDungeonDoneTimes×
Error_RegisterIsFullØ
Error_TargetFriendNumMaxÙ
Error_FriendNumMaxÚ
Error_BlackContactNumMaxÛ 
Error_FriendApplyListNumMaxÜ"
Error_GuildDonateNotReachTimeİ
Error_TalentMustChooseŞ
Error_TalentResetNoExistß
Error_GachaFreeFailedà
Error_ClickLikeCountLimitá 
Error_ClickLikeAlreadyClickâ&
!Error_ContinuousLoginDaysNotReachã&
!Error_ReceiveContinuousLoginPrizeä
Error_PostcardCellLightedå
Error_AllPostcardNotFinishæ
Error_PostcardNotFinishç
Error_SkinNotForThisActorè"
Error_YouMustOwnThisActorSkiné'
"Error_TeamDungeonRoomLevelNotReachê&
!Error_TeamDungeonGroupIdNotInWeekë+
&Error_TeamDungeonPlayerBeenInOtherRoomì1
,Error_TeamDungeonTargetPlayerBeenInOtherRoomí"
Error_TeamDungeonRoomNotExistî+
&Error_TeamDungeonTargetPlayerNotInRoomï+
&Error_TeamDungeonPlayerNotBeRoomMasterğ#
Error_TeamDungeonRoomPlayerMaxñ(
#Error_TeamDungeonPlayerHasBeenReadyò/
*Error_TeamDungeonTargetIsWaittingForChangeó/
*Error_TeamDungeonPlayerIsWaittingForChangeô,
'Error_TeamDungeonChangePosLockerTimeoutõ.
)Error_TeamDungeonRejectYourChangePosApplyö'
"Error_TeamDungeonActorListNumError÷-
(Error_TeamDungeonActorFirstPosMustBeMainø3
.Error_TeamDungeonActorSecondPosMustBeAssistantù.
)Error_TeamDungeonTwoActorCanNotBeSameRoleú+
&Error_TeamDungeonReadyMustSetTwoActorsû-
(Error_TeamDungeonStartMustHaveTwoPlayersü+
&Error_TeamDungeonStartSomebodyNotReadyı&
!Error_TeamDungeonFightServerErrorş)
$Error_TeamDungeonDungeonCreateFailedÿ.
)Error_TeamDungeonPlayerMaxCannotJoinQueue€'
"Error_TeamDungeonRoomIsInFastQueue-
(Error_TeamDungeonRoomJoinFastQueueFailed‚)
$Error_TeamDungeonPlayerIsInFastQueueƒ&
!Error_TeamDungeonRoomServerIsBusy„
Error_PlayerTypeError…%
 Error_CannotRemoveCurrentVersion†
Error_WebVoteRoleError‡ 
Error_MazeEnterLevelLimitedˆ
Error_YouHaveEnterMaze‰
Error_YouHaveNotEnterMazeŠ
Error_MazeNotSame‹
Error_MazeFloorInvalidŒ
Error_MazeMaxFloorLimited
Error_YouHaveNotReachEnd!
Error_NotGetMazeMapDimension
Error_NotGetMazeEvent
Error_NotGetMazeEventLine‘
Error_MazeSamePos’
Error_MazeInvalidPos“!
Error_MazeActorSelectLimited”!
Error_MazeYouHaveSelectActor•
Error_MazeSameActorOnlyOne–"
Error_MazeYouHaveNotThisActor— 
Error_MazeYouHaveSelectMain˜
Error_MazeActorNotSelect™
Error_GachaVerifyNotOpenš
Error_ClawCraneSellOut› 
Error_YouMustChooseActivityœ
Error_ClawNoRewardGetŸ"
Error_SpeakToTooManyStrangers &
!Error_ChipExchangeSettlePosFailed¡"
Error_JigsawPuzzleCellLighted¢#
Error_JigsawPuzzleNumNotEnough£'
"Error_JigsawPuzzlePrizeNumNotValid¤!
Error_GiftCodeDeviceNotMatch¥!
Error_GiftCodeGroupIDInvalid¦%
 Error_GiftCodeGroupIDUseReachMax§
Error_EquipMatchUnlock¨
Error_EquipNotExist©
Error_EquipConfNotExistª
Error_InvalidEquipPos«
Error_EquipOutOfLevel¬
Error_YouAreNotVip®
Error_ProgressNotEnough¯(
#Error_ActorCultivationUpgradeFailed°
Error_ActivityNotDone±
Error_ActivityDoneBefore²
Error_ActivityNotEND·$
Error_ActorTalentResetNotFinish¸
Error_KickReason_GmKickedè
Error_KickReason_TimeOuté%
 Error_KickReason_OtherWorldLoginê'
"Error_KickReason_OtherSessionLoginë
Error_KickReason_UnKownWsì"
Error_KickReason_UnKownPlayerí)
$Error_KickReason_SessionVerifyFailedî%
 Error_KickReason_ReconnectFailedï!
Error_KickReason_ServerCloseğ%
 Error_KickReason_ServerExceptionñ%
 Error_KickReason_ReconnectRejectò
Error_KickReason_Maasó
Error_FuncBan_AQ_Loginô'
"Error_FuncBan_AQ_AutoFreezeAccountõ
Error_FuncBan_AQ_Chatö
Error_FuncBan_AQ_AddFriend÷!
Error_FuncBan_AQ_AppointPlayø
Error_BuyDuplicateResù/
*Error_RoleCountNotEnoughDeployMissionCountú
Error_KickReason_GrowGuardû
Error_FuncBan_GrowGuardü"
Error_DeployMissionCountIsMaxı&
!Error_GameModeConditionNotReachedş
Error_ItemNumNotEnoughÿ
Error_ItemNotForSale€ 
Error_FuncBan_Chat_Reported
Error_DungeonNotActive‚#
Error_FavourIncreaseDailyLimitƒ#
Error_FavourIncreaseTotalLimit„
Error_ActorSkillNotFound…-
(Error_StarNotEnoughForEventDungeonReward†&
!Error_EventDungeonRewardHasGotten‡$
Error_KickReason_NeedLoginAgainĞ"
Error_RelationPointDailyLimitˆ
Error_SignboardLocked‰
Error_TriggeredEventIsCdŠ+
&Error_TriggeredEventFinishedDailyLimit‹
Error_FavourGiftNotMatchŒ!
Error_TriggeredEventNotFound!
Error_TriggeredEventNotMatch
Error_TriggeredEventExpire)
$Error_NotFoundSuitableTriggeredEvent,
'Error_ConditionRewardHasNoQualification‘$
Error_ConditionRewardAlreadyGot’#
Error_WeekActivePointNotEnough“*
%Error_WeekActivePointRewardAlreadyGot”!
Error_HomeBackGroundIsLocked•"
Error_TriggeredEventListLimit– 
Error_FriendApplyLevelLimit—%
 Error_ChallengeScoreRewardHasGot˜"
Error_ChallengeScoreNotEnough™
Error_BanChallengeModeš"
Error_GuildRentActorTypeError›*
%Error_GuildDungeonInvalidForLimitTimeœ!
Error_GuildRentActorNotExist%
 Error_GuildDungeonScoreNotEnough(
#Error_GuildDungeonScoreRewardHasGotŸ(
#Error_GuildDungeonProgressNotEnough +
&Error_GuildDungeonProgressRewardHasGot¡
Error_PerfectGachaHaveBuy¢
Error_PerfectGachaNoRecord£"
Error_PerfectGachaOpenTooFast¤
Error_BuyLimit¥"
Error_CanNotRentYourselfActor¬/
*Error_ActorNotAvailableForActorCultivation­bproto3
×
extrareward.protooptions.protocommon.protogamedb.proto
xlsx.protoxlsx_server.proto"ï
ApiExtraReward
	rule_type (
rule_params (p
result
rule_id (
drop_id (	
reward (2.STResource'
reward_merge_order (2.STResource:CÒóApiExtraRewardViewÊó/extrareward/ÚóæŸ¥è¯¢é¢å¤–å¥–åŠ±ä¿¡æ¯bproto3
3
 google/protobuf/field_mask.protogoogle.protobuf
—A
fight.protocommon.protooptions.proto"(
BuffKeyImpl

id (
owner ("¬
RoundActorBuff
buffKey (2.BuffKeyImpl
round (
stack (
state (
val (
exRound (

mergeState	 (
	mergeList
 (2.BuffKeyImpl
isNewHit (

removeType (
ownerUid (
rate (
sourceId (
indexId (

contentVal ("J
UseInfoImpl
attack (2.UseBuffActor
index (
level ("L
UseBuffInfo
target (2.UseBuffActor
infoList (2.UseInfoImpl"*
UseBuffActor
team (
seat (",
DynamicSTAttr
type (
value (")
BuffHpChange
kind (
val ("_
DynamicTalent
talentId (
talentValue (

talentFlag (
talentIndex ("‰
DynamicLinkInfo
linkID (
	ownerSeat (
	actorList (
uidList (
	totalStar (
mpVal (
spVal ("ş
RoundActorInfo
	actorInfo (2.STActor
uniqueID (
team (
seat (
isAssist (
closeID ($
dynamicAttrs (2.DynamicSTAttr!
buffList (2.RoundActorBuff
lock	 (
baseSeat (
	buffIndex (#
hpChangeList (2.BuffHpChange%
dynamicTalent (2.DynamicTalent
sequence (
baseHp (
specialIndex ("G
RoundPlayerInfo
playerId (	"
	actorList (2.RoundActorInfo"5
HitResultInfoList 
listInfo (2.HitResultInfo"7
BuffResultInfoList!
listInfo (2.RoundActorBuff"7
RoundDeadInfo

id (
seat (
kind ("É
TeamStatistic
	gameRound (
totalDamage ( 
deadInfo (2.RoundDeadInfo
	initCount (

aliveCount (
assistCount (

linkIdList (
bigSkillList (
cardNumList	 (
recordHpList
 (
buffKindList ( 
	inputList (2.UseCardInput%

damageList (2.PlayerDamageInfo"¥
RoundTeamInfo
team ($

playerList (2.RoundPlayerInfo#
allCardList (2.RoundCardInfo#
cardsInHand (2.RoundCardInfo$
cardsInStack (2.RoundCardInfo
cardShowDelay (
cardCountDown (
cardCurIndex (
	cardState (
	useResult (

hitKeyList (	)
hitResultList (2.HitResultInfoList
buffKeyList (+
buffResultList (2.BuffResultInfoList
	useLinkID (
useCard (
useSCard (
useActorList (#
useCardList (2.RoundCardInfo'
useActionList (2.PerformanceInfo
useSupportActor! (
preCardQueue (
winParam  (&
linkInfoList" (2.DynamicLinkInfo
energySp# (
energyMp$ (

activeList% (

stageParam& (
dungeonType' (!
	statistic( (2.TeamStatistic"G
RoundTeamInitData
hpList (
recordSp (
recordMp (".
SequenceActorInfo
uid (
dead ("¸
RoundCardInfo
index (
actorRuntimeID (
actorID (
sCard (
assist (
dead (
lock (
linkID (
useCount	 (

skillIndex
 ("B
LoadGameInput
playerId (	
progress (
round ("!
RoundEndInput
playerId (	"ˆ
InitGameInput
dungeonType (
stageID ( 
teamList (2.RoundTeamInfo$
initList (2.RoundTeamInitData
	guideType (
supportMode (
	debugMode (
	testActor (
playerCount	 (

randomSeed
 (

fightBuffs ("İ
FightActiveData
dungeonType (
stageID ( 
teamList (2.RoundTeamInfo 
loadList (2.LoadGameInput
	gameStart (

roundStart (
	gameRound (

activeTeam (
time	 (
init
 (
playerCount (

roundState (
useCardResult (
activePlayerID (	
supportMode (
useCardTeam (
initSeed (
winTeam (

roundIndex (
	guideType (
sequenceActor ((
sequenceList (2.SequenceActorInfo
activeActorList (
	fightSeed (
	team1Seed (
	team2Seed (
curSupportActor ("
CardAIInput
team ("Ò
PerformanceInfo
target (2.UseBuffActor

actionName (	
textDisplayDelayTime (
textContent (	
	textStyle (
performanceDuration (
newIdlePose (	
actionTiming ("Ø
CardAIOutput 
cardSend (2.RoundCardInfo
buffList (2.UseBuffInfo
	skillType (
superSkillType (
linkId (
backgroundEffectName (	-
performanceInfoList (2.PerformanceInfo"Ì
DamageInput
	attack_id (
	target_id ($
attack_attrs (2.DynamicSTAttr$
target_attrs (2.DynamicSTAttr
addrole_count (
attack_count (
attack_type ("
attack_base_attrs (2.STAttr"
target_base_attrs (2.STAttr"
attack_buff_attrs (2.STAttr"
target_buff_attrs (2.STAttr#
attack_skill_attrs (2.STAttr
skill_id (
skill_action (	
skill_level (
total_hit_count ("w
DamageOutput
result (
critical (
boundVal (
physical (
magical (
showType ("¬
HitResultInfo
action (	
skillID (
outPut (2.DamageOutput
hitCount (
boundID (
buffKey ('

buffResult (2.BuffResultInfoList"E
RequestCardInput
team (
	debugMode (
playerId (	"í
UseCardInput
linkID (
actorID (
team ( 
cardList (2.RoundCardInfo
buffList (2.UseBuffInfo$

actionList (2.PerformanceInfo
noDamage (
useCard (
useSCard	 (
	debugMode
 ("-
UseCardOutput
team (
result ("4
PlayerDamageInfo
playerId (	
damage ("Û
FightBaseInfo

dungeon_id (
create_time ("
teams (2.FightBaseInfo.TeamI

PlayerInfo
	player_id (	
actors (2.STActor
quited (2
Team*
players (2.FightBaseInfo.PlayerInfo"µ
DbBurstLinkPVPDungeon
dungeon_uid (
expire_seconds (*
teams (2.DbBurstLinkPVPDungeon.Team
state (

dungeon_id (
dungeon_type (R
Member
	player_id (	
actors (2.STActor
name (	
level (6
Team.
members (2.DbBurstLinkPVPDungeon.Member"G
FightLoginInput
	player_id (

dungeon_id (
token ("P

ScMatchNtf
fightsvr_url (	,
dungeon_info (2.DbBurstLinkPVPDungeon"D
CsInitGameReq

dungeon_id (
request (2.InitGameInput".
ScGameErrorRsp
type (	
result ("B
CsUseCardReq

dungeon_id (
request (2.UseCardInput"/
ScUseCardRsp
	useResult (
team ("J
CsRequestCardReq

dungeon_id ("
request (2.RequestCardInput"F
ScRequestCardRsp$

fight_data (2.FightActiveData
team ("E
CsLoadFightReq

dungeon_id (
request (2.LoadGameInput"F
CsFightRoundReq

dungeon_id (
request (2.RoundEndInput"'
CsLeaveDungeonReq

dungeon_id (""
ScLeaveDungeonNtf
param ("5
ScFightOverNtf
	over_type (
win_team ("J
ScFightActiveDataNtf
type (	$

fight_data (2.FightActiveData"I
CsInteractionReq

dungeon_id (
emote_id (
content (	"\
ScInteractionRsp
	player_id (	

dungeon_id (
emote_id (
content (	*˜
BuffFightType
BuffFightType_None 
BuffFightType_Attr
BuffFightType_AbHurt
BuffFightType_Remove
BuffFightType_Rebound
BuffFightType_XiShou
BuffFightType_LockCard
BuffFightType_KeepAlive
BuffFightType_Ignore
BuffFightType_Immune	
BuffFightType_HpChange

BuffFightType_MpCostChange 
BuffFightType_ActionFeedBack
BuffFightType_HpAddChange
BuffFightType_MpOnceChange#
BuffFightType_SkillDamageChange
BuffFightType_SpRateChange
BuffFightType_SpToMp
BuffFightType_StackDelete
BuffFightType_BuffValChange
BuffFightType_SkillReset
BuffFightType_SpOnceChange
BuffFightType_Sneer
BuffFightType_HpImmeChange
BuffFightType_ReduceCD
BuffFightType_Bomb
BuffFightType_CopyOrCut!
BuffFightType_BuffRoundChange#
BuffFightType_SpecialAttrChange%
!BuffFightType_EnhanceAbHurtSingle+
'BuffFightType_BuffRoundChangeOnceForAll
BuffFightType_ConjureSkip
BuffFightType_BreakConjure "
BuffFightType_ChangeRoundAddMp!*^
BuffFightState
BuffFightState_None 
BuffFightState_Alive
BuffFightState_Deletebproto3
£(
gacha.protooptions.protocommon.protogamedb.proto
xlsx.protoxlsx_server.proto"ş
DbGachaInfo
	player_id (	
trade_count (*

trade_info (2.DbGachaInfo.TradeInfo*
records (2.DbGachaInfo.RecordsEntry
plot_emote_cur_season (
is_first_plot_emote (<
actor_type_record (2!.DbGachaInfo.ActorTypeRecordEntryn
	TradeInfo
trade_id (
time (
count (

id (&

res_change (2.STResourceDynamicÚ
GachaActorPoolRecord
activity_start_time (
gacha_count (%
gacha_count_drop_purple_actor ("
gacha_count_not_get_orange (#
not_rand_limited_pool_count ( 
get_activity_actor_count (ê
GachaRecord
type_id (
count (
score_reach (
gacha_count_today (
last_gacha_time (!
gacha_activity_start_time (E
latest_precious_actors (2%.DbGachaInfo.GachaRecord.actor_record
last_free_gacha_time	 (%
gacha_count_drop_purple_actor
 ("
gacha_count_not_get_orange (#
not_rand_limited_pool_count (H
actor_pool_record (2-.DbGachaInfo.GachaRecord.ActorPoolRecordEntryU
actor_record

id (
quality (
talent_score (

time_order (Y
ActorPoolRecordEntry
key (0
value (2!.DbGachaInfo.GachaActorPoolRecord:8H
RecordsEntry
key ('
value (2.DbGachaInfo.GachaRecord:8•
GachaActorTypeRecord
last_actor_type (O
actor_type_count (25.DbGachaInfo.GachaActorTypeRecord.ActorTypeCountEntry*
ActorTypeCount
continuous_count (g
ActorTypeCountEntry
key (?
value (20.DbGachaInfo.GachaActorTypeRecord.ActorTypeCount:8Y
ActorTypeRecordEntry
key (0
value (2!.DbGachaInfo.GachaActorTypeRecord:8"«
GachaActivityInfo
activity_id (
next_get_purple_count (
cur_drop_activity_actor (
pre_drop_activity_actor ( 
get_activity_actor_count ("ò
ApiGachaInfo×
	gachainfo

id (
gacha_count_today (;
actor_record (2%.DbGachaInfo.GachaRecord.actor_record
next_free_gacha_time (
next_get_purple_count ()
activity_info (2.GachaActivityInfo2
get+

gacha_info (2.ApiGachaInfo.gachainfo:TÒóApiGachaInfoViewÊó&/gachainfo/,/gachainfo/<int:gacha_id>/Úóæ‰­è›‹åŸºæœ¬ä¿¡æ¯"™
ApiGachaPlotEmote>
result
is_first_plot_emote (
actor_video_url (	:DÒóApiGachaPlotEmoteViewÊó/gacha-plot-emote/Úóæ‰­è›‹å‰§æƒ…emote"Ü
ApiGachaTrade

id (
cost (2.STResource
count (

gacha_free (
rate (
activity_id ( 
result$
info (2.DbGachaInfo.TradeInfo
gacha_count_today (;
actor_record (2%.DbGachaInfo.GachaRecord.actor_record
add_gacha_score (
next_free_gacha_time (
is_claw_success (
next_get_purple_count ()
activity_info (2.GachaActivityInfo:;ÒóApiGachaTradeViewÊó/gachatrade/Úóæ‰­è›‹åŸºæœ¬ä¿¡æ¯"¢
ApiGachaShop
good_id (E
get
next_refresh_time (#
goods (2.GachaScoreShopGoods::ÒóApiGachaShopViewÊó/gacha-shop/Úóæ‰­è›‹å•†åº—æ“ä½œ"›
ApiGachaShopTrade
good_id (.
post&

res_change (2.STResourceDynamic:EÒóApiGachaShopTradeViewÊó/gacha-shop/trade/Úóæ‰­è›‹å•†åº—è´­ä¹°"û

ApiGachaVerify

id (
count (
activity_id ()

GachaTotal
type (
count (ƒ

GachaActorA
quality_info (2+.ApiGachaVerify.GachaActor.QualityInfoEntry2
QualityInfoEntry
key (
value (:8v
GachaPurpleActor
random_count (
force_count (
random_count_orange (
force_count_orange (A
GachaLimitActorCount
random_count (
force_count (³
GachaLimitActorF
quality_info (20.ApiGachaVerify.GachaLimitActor.QualityInfoEntryX
QualityInfoEntry
key (3
value (2$.ApiGachaVerify.GachaLimitActorCount:8E
GachaVerifyTime
verify_start_time (
verify_end_time (
result9

total_info (2%.ApiGachaVerify.result.TotalInfoEntry9

actor_info (2%.ApiGachaVerify.result.ActorInfoEntryF
purple_actor_info (2+.ApiGachaVerify.result.PurpleActorInfoEntryD
limit_actor_info (2*.ApiGachaVerify.result.LimitActorInfoEntry4
verify_time (2.ApiGachaVerify.GachaVerifyTimeL
TotalInfoEntry
key ()
value (2.ApiGachaVerify.GachaTotal:8L
ActorInfoEntry
key ()
value (2.ApiGachaVerify.GachaActor:8X
PurpleActorInfoEntry
key (/
value (2 .ApiGachaVerify.GachaPurpleActor:8V
LimitActorInfoEntry
key (.
value (2.ApiGachaVerify.GachaLimitActor:8:=ÒóApiGachaVerifyViewÊó/GachaVerify/Úóæ‰­è›‹æ¦‚ç‡éªŒè¯"á
ApiGachaPoolVerify
	pool_type (î
result=

gacha_pool (2).ApiGachaPoolVerify.result.GachaPoolEntry
cur_server_time (°
ActivityPoolP
activity_pool (29.ApiGachaPoolVerify.result.ActivityPool.ActivityPoolEntryN
ActivityPoolEntry
key ((
value (2.DropInfo.DropInfoDynamic:8Y
GachaPoolEntry
key (6
value (2'.ApiGachaPoolVerify.result.ActivityPool:8:GÒóApiGachaPoolVerifyViewÊó/GachaPoolVerify/Úóæ‰­è›‹æ± éªŒè¯(get)bproto3
¥m
gamedb.protocommon.proto
mail.proto"Ô`
DBGamePlayer
	player_id (	)
	base_info (2.DBGamePlayer.BaseInfo)
game_actors (2.DBGamePlayer.Actors/
item_package (2.DBGamePlayer.ItemPackage1
equip_package (2.DBGamePlayer.EquipPackage6
actor_group_info (2.DBGamePlayer.ActorGroupInfo4
story_mode_info (2.DBGamePlayer.StoryModeInfo+

guild_data	 (2.DBGamePlayer.GuildDataE
activity_dungeon_record
 (2$.DBGamePlayer.ActivityDungeonRecords2
async_pvp_info (2.DBGamePlayer.AsyncPvpInfo3
deploy_mission (2.DBGamePlayer.DeployMission+
medal_package (2.DBGamePlayer.Medals(

emoji_info (2.DBGamePlayer.Emojis0
headphoto_info (2.DBGamePlayer.HeadPhotos-
tutorial5 (2.DBGamePlayer.TutorialEntry:
collection_equips6 (2.DBGamePlayer.CollectionsEquips:
collection_actors7 (2.DBGamePlayer.CollectionsActors9
collection_novels8 (2.DBGamePlayer.CollectionsNovel5
nover_cover_package9 (2.DBGamePlayer.NovelCover+
profits: (2.DBGamePlayer.ProfitsEntry1
gift_got_record< (2.DBGamePlayer.GiftRecord
last_gold_dungeon_level@ (%
	maas_infoB (2.DBGamePlayer.Maas0
signboard_infoC (2.DBGamePlayer.Signboards?
event_dungeon_recordD (2!.DBGamePlayer.EventDungeonRecords:
chip_exchange_infoE (2.DBGamePlayer.ChipExchangeInfo2
fight_buffsF (2.DBGamePlayer.FightBuffsEntry>
guild_dungeon_recordG (2 .DBGamePlayer.GuildDungeonRecord4
purble_pairs_infoH (2.DBGamePlayer.PurblePairs?
equip_custom_matchI (2#.DBGamePlayer.EquipCustomMatchEntryB
DbWeekActivePoint
week_active_point (

reward_got (\
DBDelayReward
delay_reward_days (
last_reward_time (
sustained_days (ˆ
BaseRes
cur_exp (

head_photo (
title_id (
	pay_money (

free_money (
power (
gold (
medal_point (
honour_point	 (
relation_point
 (

head_frame (
guild_score (
tower_score (
achievement_point (
gacha_point (
month_card_days (
month_card_last_refresh (
gold_dun_ticket (æ
StoryModeInfo
select_actor_group_id (!
tournament_actor_group_id (#
gold_dungeon_actor_group_id ( 
async_pvp_actor_group_id (&
triggered_event_actor_group_id ($
guild_dungeon_actor_group_id (±
ActorGroupInfoC
actor_groups (2-.DBGamePlayer.ActorGroupInfo.ActorGroupsEntry
	select_id (o
Group:
members (2).DBGamePlayer.ActorGroupInfo.Group.Member*
Member
index (
	actor_uid (V
ActorGroupsEntry
key (1
value (2".DBGamePlayer.ActorGroupInfo.Group:8†
TimeIncD
last_res_inc_time (2).DBGamePlayer.TimeInc.LastResIncTimeEntry5
LastResIncTimeEntry
key (
value (:8¾	
	GuildData
guild_id (
type (
	join_time
 (
last_quit_time (
guild_score_record (
invited_guild_id (
pending_guild_id (&
last_guild_donate_balance_time (

quit_count (
limit_end_time (
donate_limit_end_time (
guild_level_changed (

rent_actor (@
rented_actors (2).DBGamePlayer.GuildData.RentedActorsEntry@
actor_group_info (2&.DBGamePlayer.GuildData.ActorGroupInfo$
guild_dungeon_actor_group_id ($
guild_dungeon_limit_end_time (İ
	RentActor
actor (2.STActor
fight_times (
	player_id (	
cultivation_degree (B
	pos_equip	 (2/.DBGamePlayer.GuildData.RentActor.PosEquipEntry/
PosEquipEntry
key (	
value (:8V
RentedActorsEntry
key (0
value (2!.DBGamePlayer.GuildData.RentActor:8é
ActorGroupInfoM
actor_groups (27.DBGamePlayer.GuildData.ActorGroupInfo.ActorGroupsEntry
	select_id (’
GroupD
members (23.DBGamePlayer.GuildData.ActorGroupInfo.Group.MemberC
Member
index (
	actor_uid (
actor_guild_uid (`
ActorGroupsEntry
key (;
value (2,.DBGamePlayer.GuildData.ActorGroupInfo.Group:8%
ItemPackage
items (2.STItem(
EquipPackage
equips (2.STEquip
CollectionsEquips;
equips (2+.DBGamePlayer.CollectionsEquips.EquipsEntry-
EquipsEntry
key (
value (:8Ã
Actors
player_actors (2.STActor
unlocked_skins (
	new_skins (:
actor_id_map (2$.DBGamePlayer.Actors.ActorIdMapEntry1
ActorIdMapEntry
key (
value (:8£
CollectionsActorsQualityQ
actors_quality (29.DBGamePlayer.CollectionsActorsQuality.ActorsQualityEntry4
ActorsQualityEntry
key (
value (:8 
CollectActorInfo
time (¶
CollectionsActorsK
actor_role_info (22.DBGamePlayer.CollectionsActors.ActorRoleInfoEntryT
ActorRoleInfoEntry
key (-
value (2.DBGamePlayer.CollectActorInfo:80
NovelActorStarInfo
star (
time (Ş
CollectionsNovelActor
	cur_cover (
cur_reward_num (F

actor_info (22.DBGamePlayer.CollectionsNovelActor.ActorInfoEntryR
ActorInfoEntry
key (/
value (2 .DBGamePlayer.NovelActorStarInfo:8¼
CollectionsNovelL
novel_actor_info (22.DBGamePlayer.CollectionsNovel.NovelActorInfoEntryZ
NovelActorInfoEntry
key (2
value (2#.DBGamePlayer.CollectionsNovelActor:8 

NovelCoverJ
player_novel_cover (2..DBGamePlayer.NovelCover.PlayerNovelCoverEntryF
PlayerNovelCoverEntry
key (
value (2.STNovelCover:8¸
Medals=
player_medals (2&.DBGamePlayer.Medals.PlayerMedalsEntry
current_fight_medal (
show_medals (=
PlayerMedalsEntry
key (
value (2.STMedal:8†
Emojis=
player_emojis (2&.DBGamePlayer.Emojis.PlayerEmojisEntry=
PlayerEmojisEntry
key (
value (2.STEmoji:8

SignboardsI
player_signboards (2..DBGamePlayer.Signboards.PlayerSignboardsEntryE
PlayerSignboardsEntry
key (
value (2.STSignboard:8

HeadPhotosI
player_headphotos (2..DBGamePlayer.HeadPhotos.PlayerHeadphotosEntryE
PlayerHeadphotosEntry
key (
value (2.STHeadPhoto:8 

OnlineInfo
last_login_time (
last_logout_time (
today_online_time (
total_online_time (

online_pid (
current_token (	Z
RefreshPriceInfo
type (

start_time (
attend_times (
params (Â
FuncBanInfo8
ban_info (2&.DBGamePlayer.FuncBanInfo.BanInfoEntry
last_chat_ban_end_time (
ban_chat_history_times (f
BanInfo
ban_type (
	ban_start (
ban_end (

ban_reason (	
	ban_param (Q
BanInfoEntry
key (0
value (2!.DBGamePlayer.FuncBanInfo.BanInfo:8Ô
ActivityDungeonRecordsO
dungeon_record (27.DBGamePlayer.ActivityDungeonRecords.DungeonRecordEntry
select_actor_group_id (`
DungeonRecord

dungeon_id (
finished (
targets_done (
multi_power (h
DungeonRecordEntry
key (A
value (22.DBGamePlayer.ActivityDungeonRecords.DungeonRecord:8Œ
EventDungeonRecordsL
dungeon_record (24.DBGamePlayer.EventDungeonRecords.DungeonRecordEntry
select_actor_group_id ( 
DungeonRecord

dungeon_id (
finished (
targets_done (
multi_power (
activity_id (
activity_type (

done_times (e
DungeonRecordEntry
key (>
value (2/.DBGamePlayer.EventDungeonRecords.DungeonRecord:8…
MidasKey
open_id (	
open_key (	

session_id (	
session_type (	

pf (	
pf_key (	
platform (j
	MidasInfo)
	midas_key (2.DBGamePlayer.MidasKey
midas_save_amt (
midas_money_change ()
AsyncPvpInfo
today_max_win_cnt (F
DropFloorInfo
score (
count (
reset_timestamp (i
MissionInfo

id (
roleid (
status (
end_timestamp (
start_timestamp (œ

DeployInfo?
mission_info (2).DBGamePlayer.DeployInfo.MissionInfoEntryM
MissionInfoEntry
key ((
value (2.DBGamePlayer.MissionInfo:8ğ
DeployMission@
deploy_info (2+.DBGamePlayer.DeployMission.DeployInfoEntry
deploy_attend_role (B
deploy_story (2,.DBGamePlayer.DeployMission.DeployStoryEntry
deploy_accomplish_num (
guild_refreshed_today (K
DeployInfoEntry
key ('
value (2.DBGamePlayer.DeployInfo:82
DeployStoryEntry
key (
value (:8†
SecTLog
pay_money_add (
pay_money_cost (
gold_add (
	gold_cost (

power_cost (
fight_count (4
HomeBackGround
current (
	is_random («
EquipCustomMatch

match_name (	L
match_equip_uids (22.DBGamePlayer.EquipCustomMatch.MatchEquipUidsEntry5
MatchEquipUidsEntry
key (
value (:8ˆ
BaseInfo
max_uid (
name (	
level (
register_time ('
base_res (2.DBGamePlayer.BaseRes
language (	(
online (2.DBGamePlayer.OnlineInfoE
last_res_inc_time  (2*.DBGamePlayer.BaseInfo.LastResIncTimeEntry
battle_room_uid	 (
last_daily_refresh_time (
last_month_refresh (
last_hour_refresh (

guide_step (C
tournament_info (2*.DBGamePlayer.BaseInfo.TournamentInfoEntry:
refresh_price_info (2.DBGamePlayer.RefreshPriceInfo
sex (
device_type (
social_open_id (	
social_platform (
last_client_version (	
last_device (	
last_telecom_oper (	
last_network (	+

midas_info (2.DBGamePlayer.MidasInfo9

drop_floor (2%.DBGamePlayer.BaseInfo.DropFloorEntry
social_picture (	
last_ipaddr (	
last_install_channel (
last_device_id' (	
exchange_star (
pvp_uid (
ban_end_time! (
gamecenter_login_time# (
gamecenter_click_time$ (
xinyue_click_time* (
race_click_time+ (
qqvip% (
notFirstRename& ( 
active_story_dungeon_uid( (0
func_ban_info) (2.DBGamePlayer.FuncBanInfo?
delay_rewards, (2(.DBGamePlayer.BaseInfo.DelayRewardsEntry
	real_imei- (	
system_software. (	
idfa/ (	'
sec_tlog0 (2.DBGamePlayer.SecTLog
pay_test_amt1 (
last_zero_refresh_time2 (
last_register_channel3 (9
week_active_info4 (2.DBGamePlayer.DbWeekActivePoint
present_target_actor5 (
anime_movie_click_time6 (6
home_back_ground7 (2.DBGamePlayer.HomeBackGround
actor_attrs_version8 ( 
friend_apply_level_limit9 (E
challenge_record: (2+.DBGamePlayer.BaseInfo.ChallengeRecordEntry
challenge_activity; ("
anime_movie_oct_click_time< ("
actor_talent_check_version= (5
LastResIncTimeEntry
key (
value (:85
TournamentInfoEntry
key (
value (:8M
DropFloorEntry
key (*
value (2.DBGamePlayer.DropFloorInfo:8P
DelayRewardsEntry
key (*
value (2.DBGamePlayer.DBDelayReward:86
ChallengeRecordEntry
key (
value (:8_
ProfitDetailInfo

id (
type (
add (
end_time (

start_time (2

ProfitInfo

start_time (
end_time (•

GiftRecordM
gift_type_got_record (2/.DBGamePlayer.GiftRecord.GiftTypeGotRecordEntry8
GiftTypeGotRecordEntry
key (
value (:8Ë
Maas
is_child (
force_rest_time_end (
period_time_start (
once_play_time_start ($
once_game_rest_time_reminded (
is_once_game_force_rest (
ready_update (z
ChipExchangeInfo
last_activity_season (
modify_count (
settle_pos_info (
exchange_pos_info (D
FightBuffInfo
buff_id (
left_cnt (
end_time (ì
GuildDungeonRecordK
dungeon_record (23.DBGamePlayer.GuildDungeonRecord.DungeonRecordEntry
next_assist_reward_time ("
next_challenge_reward_time (^
DungeonRecord
score (
finish_times (

dungeon_id (
dungeon_type (d
DungeonRecordEntry
key (=
value (2..DBGamePlayer.GuildDungeonRecord.DungeonRecord:8J
PurblePairs
data (
state (
cnt (
	timestamp (/
TutorialEntry
key (
value (:8H
ProfitsEntry
key ('
value (2.DBGamePlayer.ProfitInfo:8N
FightBuffsEntry
key (*
value (2.DBGamePlayer.FightBuffInfo:8W
EquipCustomMatchEntry
key (-
value (2.DBGamePlayer.EquipCustomMatch:8"›
DbDungeonRecords
	player_id (	<
dungeon_record (2$.DbDungeonRecords.DungeonRecordEntry<
chapter_record (2$.DbDungeonRecords.ChapterRecordEntry
unlock_all_chapter (”
DungeonRecord

dungeon_id (
unlocked (
finished (
new (
targets_done (
unlock_end_time (
random_position (
unlock_time (
multi_power	 (

done_times
 (
buy_num (
last_buy_time (
lose_num (–
ChapterRecord

chapter_id (
unlocked (
finished (
new (
star_reward_got (

total_star (
unlock_time (U
DungeonRecordEntry
key (.
value (2.DbDungeonRecords.DungeonRecord:8U
ChapterRecordEntry
key (.
value (2.DbDungeonRecords.ChapterRecord:8"7
PushMessage
seq (
msg (
params (	"8
DBPushMessage
seq (
msgs (2.PushMessage"!

ServerMisc
time_offset ("H
DbBanPlayer
	player_id (	
ban_stat (
ban_end_time ("A
LastMessageResult
	player_id (	
seq (
data ("Ì
DbTempEntranceSwitch6
switch_info (2!.DbTempEntranceSwitch.Switch_Info|
Switch_Info
type (
switch (
tips (	
is_window_entrance (

start_time (	
	stop_time (	"F
DbSocialPlatformGift
social_open_id (	
mails (2.DbMail"Ë
DbServerUpdateInfo4
	time_info (2!.DbServerUpdateInfo.TimeInfoEntryM
TimeInfoEntry
key (+
value (2.DbServerUpdateInfo.TimeInfo:80
TimeInfo

start_time (
end_time (
İ

gift.protocommon.protooptions.protogamedb.proto"™
NpcTalkRecord
sub_channel_id (:
npc_talk_record (2!.NpcTalkRecord.NpcTalkRecordEntry4
NpcTalkRecordEntry
key (
value (:8"@

DBGiftCode
code (	
	gift_type (
	use_count ("í
ApiGiftCode.
gift_code_list (2.ApiGiftCode.code_info'
	code_info
code (	
type (8
result.
gift_code_info (2.ApiGiftCode.code_info:KÒóGiftCodeViewÊó$/giftcode/,/giftcode/<int:giftType>/Úóç¤¼åŒ…ç é…ç½®"N

ApiGetGift
code (	:2ÒóPlayerGetGiftViewÊó	/giftget/Úóé¢†å–ç¤¼åŒ…bproto3
t
guide.protooptions.proto"N
ApiGuide
cur_step (
result
step (:Òó	GuideViewÊó/guide/bproto3
éG
guild.protocommon.protooptions.protogamedb.proto"Š
DbGuildData(
	base_info (2.DbGuildData.BaseInfo
player_id_invited (	<
player_id_pending2 (2!.DbGuildData.PlayerIdPendingEntry,
donate_info/ (2.DbGuildData.DonateInfo*
profits* (2.DbGuildData.ProfitsEntry(
	vote_info, (2.DbGuildData.VoteInfo*
members- (2.DbGuildData.MembersEntry8
rent_actor_info3 (2.DbGuildData.RentActorInfoEntry:
guild_fight_buff0 (2 .DbGuildData.GuildFightBuffEntry
next_dun_balance_time1 (ÿ
BaseInfo

id (
name (	
icon (	
	billboard (	
skin (
level (
exp (
president_name (	
	president (	
vice_president (	
create_time	 (
qq_group (	
members_count (
score (1
pending_info
level (

apply_time (Q
PlayerIdPendingEntry
key (	(
value (2.DbGuildData.pending_info:8
DonateSingleInfo
request_player_id (	

donate_res (2.STResource
create_time (

love_score (
last_touch_time (F
donates_count (2/.DbGuildData.DonateSingleInfo.DonatesCountEntry3
DonatesCountEntry
key (	
value (:8¡

DonateInfo
next_balance_time (5
donates (2$.DbGuildData.DonateInfo.DonatesEntryI
donate_player_info (2-.DbGuildData.DonateInfo.DonatePlayerInfoEntryM
DonatesEntry
key (	,
value (2.DbGuildData.DonateSingleInfo:8D
DonatePlayerInfo
donate_count_weekly (
player_name (	a
DonatePlayerInfoEntry
key (	7
value (2(.DbGuildData.DonateInfo.DonatePlayerInfo:8H
ProfitsEntry
key ('
value (2.DBGamePlayer.ProfitInfo:8\
VoteInfo
in_vote (
end_time
 (
players_support (	
players_kick (	“

MemberInfo
level (
last_logout_time (
guild_score (
guild_score_record (
name (	
gamecenter_login_time (G
MembersEntry
key (	&
value (2.DbGuildData.MemberInfo:8W
RentActorInfoEntry
key (0
value (2!.DBGamePlayer.GuildData.RentActor:8D
FightBuffInfo
buff_id (
left_cnt (
end_time (Q
GuildFightBuffEntry
key ()
value (2.DbGuildData.FightBuffInfo:8"å
GuildMemberInfo
	player_id (	
name (	
level (
type (
last_logout_time (
guild_score_record (
guild_score (

head_photo (
social_picture	 (	
gamecenter_login_time
 ("«
ApiGuildRecommend/
result%
result (2.DbGuildData.BaseInfo:eÒóApiGuildRecommendViewÊó2/guild-recommend/,/guild-recommend/<int:guild_id>/Úóå¸®ä¼šæ¨èåˆ—è¡¨"³
ApiGuild
name (	
skin (
icon (	
	billboard (	
new_president (	
qq_group (	ë
result(
	base_info (2.DbGuildData.BaseInfo&

res_change (2.STResourceDynamic!
members (2.GuildMemberInfo6
pending_members (2.ApiGuildMemberPending.result5
invited_members (2.ApiGuildMemberInvite.result%
skills (2.ApiGuildSkill.result,
donate_info (2.DbGuildData.DonateInfo(
	vote_info (2.DbGuildData.VoteInfo:SÒóApiGuildViewÊó/guild/,/guild/<int:guild_id>/Úóå¸®ä¼šåˆ›å»ºï¼ŒæŸ¥è¯¢, ä¿®æ”¹"ì
ApiGuildSocialGroup
qq_group_id (	
is_bound_wx (
reset_qq (
reset_wx (
joinQq (
joinWx (
result:]ÒóApiGuildSocialGroupViewÊó"/guild/<int:guild_id>/socialgroup/Úóå¸®ä¼šç¤¾äº¤ä¿¡æ¯ä¿®æ”¹"
ApiGuildSearch

id_or_name (	/
result%
result (2.DbGuildData.BaseInfo:8ÒóApiGuildSearchViewÊó/guild-search/Úóå¸®ä¼šæœç´¢"»
ApiGuildPresident
new_president (	/
result%
result (2.DbGuildData.BaseInfo:^ÒóApiGuildPresidentViewÊó /guild/<int:guild_id>/president/Úópost ä¼šé•¿æ›´æ¢ä¸ºå…¶ä»–äºº"Õ
ApiGuildMembers
	player_id (	$
get
get (2.GuildMemberInfo/
delete%
delete (2.DbGuildData.BaseInfo:×ÒóApiGuildMembersViewÊóP/guild/<int:guild_id>/members/,/guild/<int:guild_id>/members/<string:player_id>/Úóhè·å–æ‰€æœ‰å¸®ä¼šæˆå‘˜ä¿¡æ¯,putåŠ å…¥å…¬ä¼š(éœ€è¦é‚€è¯·æˆ–è€…åŒæ„),delete é€€å‡ºæˆ–è€…è¸¢å‡ºå…¬ä¼š"¯
ApiGuildMembersQuit
	player_id (	-
post%
post (2.DBGamePlayer.GuildData:VÒóApiGuildMembersQuitViewÊó#/guild/<int:guild_id>/members/quit/Úóposté€€å‡ºå…¬ä¼š"¨
ApiGuildVicePresident
	player_id (	/
result%
result (2.DbGuildData.BaseInfo:ÊÒóApiGuildVicePresidentViewÊó^/guild/<int:guild_id>/vice-president/,/guild/<int:guild_id>/vice-president/<string:player_id>/ÚóGè·å–æ‰€æœ‰å‰¯ä¼šé•¿,put ä¼šé•¿æ·»åŠ å‰¯ä¼šé•¿ï¼Œdeleteé™çº§å‰¯ä¼šé•¿"Õ
ApiGuildPlayer
guild_level_changed (1
result'
result (2.DBGamePlayer.GuildData:sÒóApiGuildPlayerViewÊó0/guild-player/,/guild-player/<string:player_id>/Úó%ä¸ªäººå¸®ä¼šä¿¡æ¯,å¯æŸ¥çœ‹åˆ«äººçš„"ê
ApiGuildPlayerInvited/
result%
result (2.DbGuildData.BaseInfo:ŸÒóApiGuildPlayerInvitedViewÊó</guild-player/invited/,/guild-player/invited/<int:guild_id>/Úó>çœ‹å“ªäº›å·¥ä¼šé‚€è¯·äº†æˆ‘,delete æ‹’ç»æŸä¸ªå¸®ä¼šçš„é‚€è¯·"ô
ApiGuildMemberPending
	player_id (	x
result
	player_id (	
name (	
level (

apply_time (

head_photo (
social_picture (	:ÍÒóApiGuildMemberPendingViewÊó`/guild/<int:guild_id>/pending-members/,/guild/<int:guild_id>/pending-members/<string:player_id>/ÚóHç­‰å¾…å®¡æ‰¹åˆ—è¡¨,putç”³è¯·åŠ å…¥,ç®¡ç†å‘˜åŒæ„ååŠ å…¥,deleteæ‹’ç»"Ô
ApiGuildMemberInvite
	player_id (	)
result
	player_id (	
name (	:~ÒóApiGuildMemberInviteViewÊó&/guild/<int:guild_id>/invited-members/Úó4é‚€è¯·åˆ—è¡¨,puté‚€è¯·åŠ å…¥ï¼Œç©å®¶åŒæ„ååŠ å…¥"Œ
ApiGuildDonate1
result'
result (2.DbGuildData.DonateInfo:GÒóApiGuildDonateViewÊó/guild/<int:guild_id>/donate/ÚóæåŠ©åˆ—è¡¨"Â
ApiGuildDonateRequest
res (2.STResource3
post+
post (2.DbGuildData.DonateSingleInfo:ZÒóApiGuildDonateRequestViewÊó%/guild/<int:guild_id>/donate/request/Úópostå‘èµ·æ±‚åŠ©"®
ApiGuildDonateSend
res (2.STResource
player_id_request (	Œ
post-
donate (2.DbGuildData.DonateSingleInfoJ
donate_player_info (2..ApiGuildDonateSend.post.DonatePlayerInfoEntry&

res_change (2.STResourceDynamica
DonatePlayerInfoEntry
key (	7
value (2(.DbGuildData.DonateInfo.DonatePlayerInfo:8:TÒóApiGuildDonateSendViewÊó"/guild/<int:guild_id>/donate/send/Úópostå¤§ä½¬æåŠ©"¡
ApiGuildDonateTouch3
post+
post (2.DbGuildData.DonateSingleInfo:UÒóApiGuildDonateTouchViewÊó#/guild/<int:guild_id>/donate/touch/ÚóçŒ¥çæ‘¸ä¸€æ‘¸"¶
ApiGuildVotePresident
vote_support (/
result%
result (2.DbGuildData.VoteInfo:VÒóApiGuildVotePresidentViewÊó/guild/<int:guild_id>/vote/ÚópostæŠ•ç¥¨å¼¹åŠ¾ä¼šé•¿"•
ApiGuildCreateBindGroup
idip_areaid (	
idip_ip (	
	idip_port (	]
post
ret (
msg (	

group_name (	
group_number (	
	errorcode (:bÒóApiGuildCreateBindGroupViewÊó'/guild/<int:guild_id>/group/createbind/Úóåˆ›å»ºå¹¶ç»‘å®šQQç¾¤"Ü
ApiGuildUnlinkGroup
idip_areaid (	
idip_ip (	
	idip_port (	3
post
ret (
msg (	
	errorcode (:WÒóApiGuildUnlinkGroupViewÊó#/guild/<int:guild_id>/group/unlink/Úóè§£é™¤ç»‘å®šQQç¾¤"•
GuildRecord
	player_id (	
player_id_operated (	-
	operation (2.GuildRecord.OperationType
time (
param ("Š
OperationType
Default 

Create
Join
Quit
SetPresident
SetVicePresident
	SetMember
Kick
LevelUp"ˆ
ApiGuildRecord'
result
content (	
time (:MÒóApiGuildRecordViewÊó/guild/<int:guild_id>/record/Úóè·å–å…¬ä¼šè®°å½•"š
ApiGuildSkill

id (
	use_count (
result

id (
max_use_count (

score_cost (
type (
add (
time (

start_time (
end_time (‘
post/
guild_skill_info (2.ApiGuildSkill.result(
	base_info (2.DbGuildData.BaseInfo.
profit (2.DBGamePlayer.ProfitDetailInfo:EÒóApiGuildSkillViewÊó/guild/<int:guild_id>/skill/Úóå…¬ä¼šæŠ€èƒ½"y
ApiGuildPlayerReset
	player_id (	:OÒóApiGuildPlayerResetViewÊó/guild-player-reset/Úóé‡ç½®ç©å®¶å·¥ä¼šä¿¡æ¯"Õ
ApiGuildRentActor
	actor_uid (
actor_guild_uid (

actor_kind (A
get:
rent_actor_info (2!.DBGamePlayer.GuildData.RentActoræ
post:
rent_actor_info (2!.DBGamePlayer.GuildData.RentActorG
rented_actor_info (2,.ApiGuildRentActor.post.RentedActorInfoEntryY
RentedActorInfoEntry
key (0
value (2!.DBGamePlayer.GuildData.RentActor:8:TÒóApiGuildRentActorViewÊó!/guild/<int:guild_id>/rent-actor/Úóå…¬ä¼šç§Ÿå€Ÿè§’è‰²"Ú
ApiGuildDungeonActorGroupD
members (23.DBGamePlayer.GuildData.ActorGroupInfo.Group.MemberA
actor_groups (2+.ApiGuildDungeonActorGroup.ActorGroupsEntry`
ActorGroupsEntry
key (;
value (2,.DBGamePlayer.GuildData.ActorGroupInfo.Group:8F
result<
result (2,.DBGamePlayer.GuildData.ActorGroupInfo.Group:‰ÒóGuildDungeonActorGroupViewÊóQ/guild/GuildDungeonActorGroup/<int:actor_group_id>,/guild/GuildDungeonActorGroup/Úóå…¬ä¼šå‰¯æœ¬é˜µå®¹bproto3
¡
instance.protooptions.protocommon.protofight.proto"±

ApiUseCard
request (2.UseCardInput&
post
post (2.FightActiveData:[Òó
ApiUseCardÊó!/instance/<int:room_uid>/usecard/Úó$ä½¿ç”¨å¡ç‰‡ï¼Œè¿”å›FightActiveData"Å
ApiRequestCard"
request (2.RequestCardInput&
post
post (2.FightActiveData:gÒóApiRequestCardViewÊó%/instance/<int:room_uid>/requestcard/Úó$è¯·æ±‚å¡ç‰‡ï¼Œè¿”å›FightActiveData"š
ApiTestDamage
request (2.DamageInput#
post
post (2.DamageOutput:EÒóApiTestDamageÊó/instance/TestDamage/ÚóPost æµ‹è¯•ä¼¤å®³è„šæœ¬"
ApiFightActiveData$
get
get (2.FightActiveData:bÒóApiFightActiveDataÊó$/instance/<int:room_uid>/activedata/Úó getè·å¾—å½“å‰Fight active Data"ğ
ApiPushFightActiveDataM
post
last_push_seq (
data (2.FightActiveData
action (	:†ÒóApiPushFightActiveDataViewÊó$/instance/<int:room_uid>/activedata/Úó<post Long Polling æ–¹å¼è·å¾—å½“å‰æ‰€æœ‰æˆ˜æ–—åŠ¨æ€æ•°æ®"™
ApiInitFight
request (2.InitGameInput
post:`ÒóInitFightViewÊó/instance/<int:room_uid>/init/Úó)post å®¢æˆ·ç«¯å‘é€åˆå§‹åŒ–æˆ˜æ–—æ•°æ®"”
ApiLoadFight
request (2.LoadGameInput
post:[ÒóLoadFightViewÊó/instance/<int:room_uid>/load/Úó$post å®¢æˆ·ç«¯å‘é€loadæˆ˜æ–—æ•°æ®"}
ApiFightRound
request (2.RoundEndInput
post:CÒóFightRoundViewÊó/instance/<int:room_uid>/round/Úó
post roundbproto3
Ÿ
jigsawpuzzle.protooptions.protocommon.protoxlsx_server.proto
xlsx.proto"œ
DBJigsawPuzzle
	player_id (	
current_season (
is_first_enter (0
	cell_info (2.DBJigsawPuzzle.CellInfoEntry2

prize_info (2.DBJigsawPuzzle.PrizeInfoEntry/
CellInfoEntry
key (
value (:80
PrizeInfoEntry
key (
value (:8"Ü
ApiJigsawPuzzleM
result
lighten_cell (
receive_prize (
is_first_enter (:zÒóApiJigsawPuzzleViewÊó/JigsawPuzzle/ÚóMè·å–æ‹¼å›¾ä¿¡æ¯(get),å‘ŠçŸ¥serveré¦–æ¬¡è¿›å…¥(post),gmé‡ç½®æ‹¼å›¾(delete)"è
ApiJigsawPuzzleLighten
cell_id (0
result&

res_change (2.STResourceDynamic:ŠÒóApiJigsawPuzzleLightenViewÊó/JigsawPuzzle/Lighten/ÚóNç‚¹äº®æ‹¼å›¾(post),gmæ‹¼å›¾å…¨ç‚¹äº®(patch),gmé‡ç½®æ‹¼å›¾å•ä¸ªæ ¼å­(delete)"Û
ApiJigsawPuzzleReceive
	light_num (0
result&

res_change (2.STResourceDynamic:|ÒóApiJigsawPuzzleReceiveViewÊó/JigsawPuzzle/ReceivePrize/Úó;é¢†å–æ‹¼å›¾å¥–åŠ±(post),gmé‡ç½®æ‹¼å›¾ç‚¹äº®å¥–åŠ±(delete)bproto3
É

maas.protooptions.protocommon.proto"K
ApiMaas
result
result (:&ÒóMaasViewÊó/maas/Úóå¥åº·æ¸¸æˆ"È
ApiZk
zk_type (
zk_trace_id (	
zk_title (	
zk_msg (	
zk_begin_time (
zk_end_time (
zk_json_str (	
result
result (:ÒóZkViewÊó/zk/Úóä¸­æ§bproto3


mail.protocommon.protooptions.proto"¯
DbMail

id (
title (	
content (	
sender_name (	
create_time (

start_time (
end_time (
expire_time ('
attachments (2.STResourceDynamic
	important
 (
deleted (
read (
attachments_got (
global_mail (
attachments_reason (
	level_min (
attachments_count (
global_mail_type (
version (	
global_mail_id ("ª
DbPlayerMailInfo
	player_id (	
	id_latest (
	checktime (
not_reach_time_mails (
id_next (
mails (2.DbMail
delete_by_self ("§
ApiMailAutoDelete
auto_delete (0
result
auto_delete (
	del_mails (:KÒóApiMailAutoDeleteViewÊó/mail-auto-delete/Úóé‚®ä»¶è‡ªåŠ¨åˆ é™¤åŠŸèƒ½"µ
ApiMail
read (
attachments_got	 (u
result
result (2.DbMail&

res_change (2.STResourceDynamic
	del_mails (
attachments_got (
get
get (2.ScMails:nÒóApiMailViewÊó/mail/<int:mail_id>/,/mail/Úó<å•ä¸ªé‚®ä»¶æ‹‰å–,patchå¯ä»¥é¢†å–é™„ä»¶æˆ–è€…æ ‡è®°å·²è¯»"€
ApiMailGmSocialPlatform
request (2.DbMail!
result
result (2.DbMail:§ÒóApiMailGmSocialPlatformViewÊó_/mail-gm-social/<string:social_open_id>/,/mail-gm-social/<string:social_open_id>/<int:mail_id>/Úó!å•ä¸ªç©å®¶å¹³å°é¢„ç™»é™†é‚®ä»¶"¹
	ApiMailGm
title (	
content (	
sender_name (	
	important
 (

start_time (
end_time ('
attachments (2.STResourceDynamic!
result
result (2.DbMail:rÒóApiMailGmViewÊóG/mail-gm/<string:player_id>/,/mail-gm/<string:player_id>/<int:mail_id>/Úóå•ä¸ªç©å®¶é‚®ä»¶"ß
ApiMailGmGlobal
title (	
content (	
sender_name (	
	important (

start_time (
end_time (
	level_min (
global_mail_type	 (
version
 (	'
attachments (2.STResourceDynamic!
result
result (2.DbMail:TÒóApiMailGmGlobalViewÊó)/mail-gm-all/,/mail-gm-all/<int:mail_id>/Úóå…¨æœé‚®ä»¶"º
ApiAddGlobalMailIndex
	mail_list (
result
	mail_list (:qÒóApiAddGlobalMailIndexViewÊó/add-mail-index/Úó<ä¿®å¤åˆ é™¤å…¨æœé‚®ä»¶åçš„åç»­å…¨æœé‚®ä»¶é¢†å–é—®é¢˜"G
ScMails
	clear_all (
	del_mails (
mails (2.DbMail*u
GlobalMailTypeEnum
GlobalMailType_None 
GlobalMailType_All
GlobalMailType_New
GlobalMailType_Oldbproto3
…

mall.protooptions.protocommon.protogamedb.proto
xlsx.protopurchase.protoxlsx_server.proto"Ò

DbMallInfo
	player_id (	)
summary (2.DbMallInfo.SummaryEntry=
GoodSummary
	buy_count (
last_buy_start_time (G
SummaryEntry
key (&
value (2.DbMallInfo.GoodSummary:8"™
ApiMallTrade

id (C
result&

res_change (2.STResourceDynamic
	buy_count (:8ÒóApiMallTradeViewÊó/mall-trade/Úóå•†å“è´­ä¹°post"£
ApiMallInfo`
result 
mall_record (2.DbMallInfo4
purchase_record (2.DBPurchaseProductBuyRecord:2ÒóApiMallInfoViewÊó/mall-info/Úóå•†åŸæ‘˜è¦bproto3
º
match.protooptions.proto"“
ApiBurstLinkPVPMatch3
result

match_time (
max_wait_time (:FÒóBurstLinkPVPMatchViewÊó/BurstLinkPVPMatch/Úóè¶…é¢‘è¿çº¿åŒ¹é…b1«\…8¦
ê

maze.protocommon.protooptions.proto"æ	
DbMaze
	player_id (	'
	base_info (2.DbMaze.MazeBaseInfo0
select_actors (2.DbMaze.SelectActorsEntry'
fight_group (2.DbMaze.FightGroup.
enemy_record (2.DbMaze.EnemyRecordEntry*

maze_cells (2.DbMaze.MazeCellsEntry×
MazeBaseInfo
maze_id (
maze_difficulty (

maze_floor (
maze_map_dimension (
maze_map_buff_id (0
	buff_data (2.DbMaze.MazeBaseInfo.BuffData
player_cell_index (
is_maze_over (
prize_id	 (
trigger_cell_index
 (B
BuffData
buff_id (

left_round (
	actor_uid (ˆ

FightGroup*
members (2.DbMaze.FightGroup.Member
energySp (
energyMp (*
Member
index (
	actor_uid (@
SelectActor
actor (2.STActor

hp (
dead (
EnemyRecord

hp (H
SelectActorsEntry
key ("
value (2.DbMaze.SelectActor:8G
EnemyRecordEntry
key ("
value (2.DbMaze.EnemyRecord:8Ï
MazeCell

node_index (
pre_cell_index (
next_cell_index (
branch_index (
is_complete (
event_id (

event_type (
event_param (	
pre_condition_id	 (B
MazeCellsEntry
key (
value (2.DbMaze.MazeCell:8"E
CurBuffData
buff_id (

left_round (
	actor_uid ("½
MazeResponseInfo4

maze_cells (2 .MazeResponseInfo.MazeCellsEntry
maze_map_dimension (
maze_map_buff_id (
is_maze_over (
cur_buff (2.CurBuffData
player_cell_index (
prize_id (
cur_maze_floor (B
MazeCellsEntry
key (
value (2.DbMaze.MazeCell:8"¤
ApiMazeSummaryX
result
maze_id (
is_select_actor ($
	maze_info (2.MazeResponseInfo:8ÒóApiMazeSummaryViewÊó/Maze/Summary/Úóè¿·å®«æ¦‚è¦"ê
ApiMazeEnter
maze_id (

maze_floor (V
result&

res_change (2.STResourceDynamic$
	maze_info (2.MazeResponseInfo:]ÒóApiMazeEnterViewÊó/Maze/Enter/Úó5è¿›å…¥è¿·å®«(post-è¿·å®«æŠ¥åï¼Œdelete-é‡ç½®è¿·å®«)"Æ
ApiMazeActor

actor_uids (4
result*
select_actors (2.DbMaze.SelectActor:lÒóApiMazeActorViewÊó/Maze/Actor/ÚóDè¿·å®«è§’è‰²(get-è·å¾—å·²é€‰è¿·å®«è§’è‰²ï¼Œpost-é€‰æ‹©è¿·å®«è§’è‰²)"ç
ApiMazeFightActorGroup*
members (2.DbMaze.FightGroup.Member”
resultC
fight_group (2..ApiMazeFightActorGroup.result.FightGroupEntryE
FightGroupEntry
key (!
value (2.DbMaze.FightGroup:8:‰ÒóApiMazeFightActorGroupViewÊó/Maze/FightActorGroup/ÚóMè¿·å®«å‡ºæˆ˜è§’è‰²ç»„(get-è·å¾—å‡ºæˆ˜è§’è‰²ç»„ï¼Œpost-é€‰æ‹©å‡ºæˆ˜è§’è‰²ç»„)"ä
ApiMazeMove
start_cell_index (
end_cell_index (ë
result
way (&

res_change (2.STResourceDynamic9
influence_buff (2!.ApiMazeMove.result.InfluenceBuff
new_buff ($
	maze_info (2.MazeResponseInfo9
InfluenceBuff
buff_id (
influence_round (:5ÒóApiMazeMoveViewÊó/Maze/Move/Úóè¿·å®«ä¸­ç§»åŠ¨"—
ApiMazeReceivePrize0
result&

res_change (2.STResourceDynamic:NÒóApiMazeReceivePrizeViewÊó/Maze/ReceivePrize/Úóé¢†å–è¿·å®«å¥–åŠ±(post)"
ApiMazeBuff
buff_id ((
result
cur_buff (2.CurBuffData:5ÒóApiMazeBuffViewÊó/Maze/Buff/ÚógmåŠ buff(post)bproto3
è
notepad.protocommon.protooptions.protogamedb.proto"Ø
DBNotePadInfo
	signboard (
check_signboard_time (7
roles_notepad (2 .DBNotePadInfo.RolesNotepadEntry
unlocked_monologue (
last_congrats (7
role_presents (2 .DBNotePadInfo.RolePresentsEntry
random_present_count (
random_present_time (

present_id	 (0
favour_limit
 (2.DBNotePadInfo.FavourLimit
roles_notepad_flag (÷
RoleNotePadInfo
role_id (
favour (
favour_level (<
notepad_events (2$.DBNotePadInfo.RoleNotePadInfo.Event
unlock_time (
present_state (
read_tag (1
Event
type (
text (	
time (b
Present

present_id (
role_id (
present_type (
rewards (2.STResourceÓ
FavourLimit?
daily_limit (2*.DBNotePadInfo.FavourLimit.DailyLimitEntry?
total_limit (2*.DBNotePadInfo.FavourLimit.TotalLimitEntry
daily_refresh_time (
first_record (	A
favour_check (2+.DBNotePadInfo.FavourLimit.FavourCheckEntryŠ
FavourCheckI

check_info (25.DBNotePadInfo.FavourLimit.FavourCheck.CheckInfoEntry0
CheckInfoEntry
key (
value (:81
DailyLimitEntry
key (
value (:81
TotalLimitEntry
key (
value (:8Z
FavourCheckEntry
key (5
value (2&.DBNotePadInfo.FavourLimit.FavourCheck:8S
RolesNotepadEntry
key (-
value (2.DBNotePadInfo.RoleNotePadInfo:8K
RolePresentsEntry
key (%
value (2.DBNotePadInfo.Present:8"´
ApiRoleNotePad>
result4
role_notepad (2.DBNotePadInfo.RoleNotePadInfo:bÒóRoleNotePadViewÊó)/RoleNotePad/,/RoleNotePad/<int:role_id>/Úóè·å–è§’è‰²æ‰‹è´¦ç›¸å…³ä¿¡æ¯"‚
ApiRoleNotePadRead
read_tag (:ZÒóRoleNotePadReadViewÊó/RoleNotePadRead/<int:role_id>/Úóput-è®¾ç½®è§’è‰²æ‰‹è´¦å·²è¯»"Ğ
ApiSignboard
	signboard (X
result
	signboard (
check_signboard_time (
unlocked_signboard_id (:SÒóSignboardViewÊó/Signboard/Úó/get-è·å–å½“å‰çœ‹æ¿å¨˜ï¼Œput-è®¾ç½®çœ‹æ¿å¨˜"¥
ApiFavourGift3
used_item_list (2.ApiFavourGift.UsedItemInfo)
UsedItemInfo

id (
count (f
result4
role_notepad (2.DBNotePadInfo.RoleNotePadInfo&

res_change (2.STResourceDynamic:LÒóFavourGiftViewÊó/FavourGift/<int:role_id>/Úópatch-é€å¥½æ„Ÿåº¦ç¤¼ç‰©"‘
ApiRolePresentZ
result(
presents (2.DBNotePadInfo.Present&

res_change (2.STResourceDynamic4
redpoint(
presents (2.DBNotePadInfo.Present:mÒóRolePresentViewÊó,/RolePresent/,/RolePresent/<int:present_id>/Úó&get-è·å–ç¤¼ç‰©åˆ—è¡¨, post-é¢†ç¤¼ç‰©bproto3
õ
notice.protocommon.protooptions.protogamedb.proto"‹
DbPlayerNotice
	player_id (	
reward_read_notices (
reward_share_notices (
share_notices (
read_notices ("T
DbNoticeReward
	notice_id (
read_reward_id (
share_reward_id ("H
ApiNoticeGetSession:1ÒóNoticeGetSessionÊó/NoticeGetSession/Úóget"ç
ApiNoticeRewardSummaryğ
result<
rewards (2+.ApiNoticeRewardSummary.result.NoticeReward§
NoticeReward
value (
name (	C
rewards (22.ApiNoticeRewardSummary.result.NoticeReward.Reward5
Reward
number (
image (	
name (	:ZÒóNoticeRewardSummaryViewÊó/NoticeRewardSummary/Úó"get-è·å–å…¬å‘Šå¥–åŠ±ç›¸å…³ä¿¡æ¯"ç
ApiPlayerNoticeReward
result
ret (:¶ÒóPlayerNoticeRewardViewÊó6/PlayerNoticeReward/<int:notice_id>/<int:reward_type>/Úó^get-è·å–ç©å®¶å…¬å‘Šå¥–åŠ±ä¿¡æ¯, post-é¢†å–å…¬å‘Šå¥–åŠ±, type-å¥–åŠ±ç±»å‹,åˆ†äº«ï¼Œé˜…è¯»"ı
ApiPlayerNoticeShareStatus9
notices (2(.ApiPlayerNoticeShareStatus.notice_share0
notice_share
	notice_id (
index (G
notice_status
	notice_id (
share_status (
index (D
result:
notices (2).ApiPlayerNoticeShareStatus.notice_status:cÒóPlayerNoticeShareStatusViewÊó/PlayerNoticeShareStatus/Úó#post-è·å–ç©å®¶åˆ†äº«å…¬å‘ŠçŠ¶æ€"„
ApiPlayerNoticeShare
result
ret (:UÒóPlayerNoticeShareViewÊó#/PlayerNoticeShare/<int:notice_id>/Úópost-åˆ†äº«å…¬å‘Š"ø
ApiPlayerNoticeReadStatus7
notices (2&.ApiPlayerNoticeReadStatus.notice_read/
notice_read
	notice_id (
index (F
notice_status
	notice_id (
read_status (
index (C
result9
notices (2(.ApiPlayerNoticeReadStatus.notice_status:dÒóPlayerNoticeReadStatusViewÊó/PlayerNoticeReadStatus/Úó&post-è·å–ç©å®¶é˜…è¯»è¯¥å…¬å‘ŠçŠ¶æ€"
ApiPlayerNoticeRead
result
ret (:SÒóPlayerNoticeReadViewÊó"/PlayerNoticeRead/<int:notice_id>/Úópost-é˜…è¯»å…¬å‘Š"©
ApiBindNoticeReward
result
ret (:{ÒóBindNoticeRewardViewÊóD/BindNoticeReward/<int:notice_id>/<int:reward_id>/<int:reward_type>/Úópost-ç»‘å®šå…¬å‘Šå¥–åŠ±bproto3
î

online.protooptions.protocs.proto"`
	DbRequest
key (	
path (	
response (	
expire_seconds (
status ("R
DbPlayerEvent
	player_id (	
event (
time_ms (
params (""
DbProcessInfo
	last_tick ("t
DbPlayerOnline
	player_id (	

session_id (
current_pid (
	last_tick (
kick_reason ("¿
DbPlayerOnlineContext
pid (
	player_id (	/
control (2.DbPlayerOnlineContext.Control
chat_sub_channel_id (
device_type (
social_platform (‰
Control
seq_cs_recv (

seq_sc_cur (
last_heart_beat (
last_active (

session_id (
last_update ("8
DbPlayerSession
	player_id (	

session_id ("Ê
DbTeamPlayerOnlineContext
pid (
	player_id (	3
control (2".DbTeamPlayerOnlineContext.Control
chat_sub_channel_id (
scmsgs (2
.ScMessage
device_type (‰
Control
seq_cs_recv (

seq_sc_cur (
last_heart_beat (
last_active (

session_id (
last_update ("
DbConnectContext
	player_id (	
seq_cs (
seq_sc (

session_id (
scmsgs (2
.ScMessage
last_heart_beat ("9
ScMessageRecord
pid (
seq (
data ("X
DbWebSocketSvrState
	server_id (
url (	
load (
last_update (bproto3
Ù	
options.proto google/protobuf/descriptor.proto:8
no_reflection.google.protobuf.MessageOptionsáÔ (:?
csharp_generate_code.google.protobuf.MessageOptionsâÔ (:;
csharp_auto_load.google.protobuf.MessageOptionsãÔ (:/
urls.google.protobuf.MessageOptions¹ (	:/
view.google.protobuf.MessageOptionsº (	:2
comment.google.protobuf.MessageOptions» (	:6
disable_log.google.protobuf.MessageOptions¼ (:.
tmd.google.protobuf.MessageOptions¾ (	:3
tm_order.google.protobuf.MessageOptions¿ (:=
disable_tlog_trans.google.protobuf.MessageOptionsÀ (:6
tlog_header.google.protobuf.MessageOptionsÁ (:5

post_cache.google.protobuf.MessageOptionsÂ (:.
tmc.google.protobuf.MessageOptionsÃ (	:+
pk.google.protobuf.FieldOptionsáÔ (:+
fc.google.protobuf.FieldOptionsÊÜ (	:2
	hide_desc.google.protobuf.FieldOptionsÓ% (:,
tfi.google.protobuf.FieldOptionsÔ% (	:,
tfs.google.protobuf.FieldOptionsÕ% (	:-
tfdv.google.protobuf.FieldOptionsÖ% (	:,
tfd.google.protobuf.FieldOptions×% (	:*
ec.google.protobuf.EnumOptionsáÔ (	:0
te_order.google.protobuf.EnumOptionsâÔ (:0
evc!.google.protobuf.EnumValueOptionsáÔ (	bproto3
Œ*
playeraction.protooptions.protocommon.protogamedb.proto
xlsx.proto"r
ApiShutDown%
result
result (
msg (	:<ÒóshutDownViewÊó
/shutDown/Úóè·å–è®¿é—®è€…çš„ipåœ°å€"Z

ApiKickAll
result
result (:2ÒóKickAllViewÊó	/kickall/Úóè¸¢æ‰æ‰€æœ‰ç©å®¶"º
ApiTempEntranceSwitch=
get6
switch_info (2!.DbTempEntranceSwitch.Switch_Info
post
result (:JÒóTempEntranceSwitchViewÊó/temp-entrance-switch/Úóä¸´æ—¶å…¥å£å¼€å…³"n
ApiDirtyCheck
result
result (	:CÒóDirtyCheckViewÊó/dirtycheck/<string:content>/Úóè„å­—å±è”½"“
ApiChangeServerTime
target_time ($
result
server_time_second (:AÒóChangeServerTimeViewÊó/changetime/Úóæ”¹å˜æœåŠ¡å™¨æ—¶é—´"ƒ
ApiPlayerSyncMidasInfo*

midas_info (2.DBGamePlayer.MidasKey:=ÒóPlayerSyncMidasInfoViewÊó/syncmidas/Úóç±³å¤§å¸ˆåŒæ­¥"”
ApiPlayerASMIadInfo
attribution (	
org_name (	
campaign_id (	
campaign_name (	
conversion_date (	

click_date (	

adgroup_id (	
adgroup_name (	
key_word	 (	:AÒóPlayerASMIadInfoViewÊó/asmiadinfo/Úóiosæœç´¢ä¸‹è½½ä¿¡æ¯"~
ApiPlayerLogin
result
	login_ret (:OÒó	LoginViewÊó/login/Úó3é€šè¿‡accountæ‹¿åˆ°çš„tokenè¿›è¡Œæ¸¸æˆç™»é™†è¯·æ±‚"{
ApiPlayerLogout
result

logout_ret (:JÒóPlayerLogoutViewÊó/logout/Úó&PushServerè°ƒç”¨,é€šçŸ¥Fciç©å®¶ç™»å‡º"Ã
ApiPlayerBaseInfoæ
result
level (
name (	
last_login_time (
social_picture (	

head_photo (
fight_medal (
sex (6
home_back_ground (2.DBGamePlayer.HomeBackGround 
friend_apply_level_limit	 (:EÒóPlayerBaseInfoViewÊó
/baseinfo/Úóplayerçš„baseinfoçš„GETæ“ä½œ"°
ApiPlayerName
name (	
get
notFirstRename (=
patch
name (	&

res_change (2.STResourceDynamic:3ÒóPlayerNameViewÊó/name/Úóä¿®æ”¹ç©å®¶åå­—"²
ApiPlayerSocialPicture
social_picture (	
sex (-
result
sex (
social_picture (	:DÒóPlayerSocialPictureViewÊó/socialpicture/ÚóåŒæ­¥ç¤¾äº¤å¤´åƒ"“
ApiPlayerLevel
level (
result
result (:XÒóApiPlayerLevelViewÊó/baseresource/level/Úó&playerçš„levelçš„æ“ä½œ,gmä¿®æ”¹ç­‰çº§"}
ApiGmRemovePlayer
	player_id (	
result:KÒóApiGmRemovePlayerViewÊó/remove-player/Úóåˆ é™¤è¯¥è´¦å·æ¸¸æˆæ•°æ®"É
ApiPlayerIdByName
player_name (	O
result
	player_id (	
entry (	

account_id (	
open_id (	:NÒóPlayerIdByNameViewÊó/playeridbyname/Úó æ ¹æ®ç©å®¶åå­—æŸ¥è¯¢ç©å®¶id"¯
ApiOpenIdByPlayerId
	player_id (	1
result
	player_id (	
msdk_open_id (	:RÒóOpenIdByPlayerIdViewÊó/openidbyplayerid/Úó æ ¹æ®ç©å®¶idæŸ¥è¯¢msdk open id"ë
ApiBanPlayer
	player_id (	
ban_stat (
ban_end_time (S
result
result (
	player_id (	
ban_stat (
ban_end_time (:KÒóApiBanPlayerViewÊó)/banplayer/,/banplayer/<string:playerId>/Úóå°å·"ß
ApiPlayerFuncBan
ban_type (
ban_start_time (
ban_end_time (

ban_reason (	:
result0
func_ban_info (2.DBGamePlayer.FuncBanInfo:;ÒóApiPlayerFuncBanViewÊó	/funcban/Úóç©å®¶åŠŸèƒ½å°åœ"¨
ApiKickPlayer
	player_id (	+
result
result (
	player_id (	:WÒóApiKickPlayerViewÊó+/kickplayer/,/kickplayer/<string:playerId>/Úóè¸¢ç©å®¶ä¸‹çº¿"Â
ApiGameCenter
social_token (	
is_gamecenter_login (
is_xinyue_click (
is_race_click (
is_anime_movie_click ( 
is_anime_movie_oct_click (Í
result
gamecenter_login_time (
gamecenter_click_time (
xinyue_click_time (
qqvip (
race_click_time (
anime_movie_click_time ("
anime_movie_oct_click_time (:>ÒóApiGameCenterViewÊó/gamecenter/ÚóQQ,wechatå¹³å°ä¿¡æ¯"
ApiXinyueVipInfo2
result
ret (
msg (	
ilevel (:HÒóApiXinyueVipInfoViewÊó/xinyue-vipinfo/Úóå¿ƒæ‚¦ä¼šå‘˜ä¿¡æ¯æŸ¥è¯¢"û
ApiServerUpdateInfo•
result<
	time_info (2).ApiServerUpdateInfo.result.TimeInfoEntryM
TimeInfoEntry
key (+
value (2.DbServerUpdateInfo.TimeInfo:8:LÒóApiServerUpdateInfoViewÊó/server-update-info/ÚóæœåŠ¡å™¨æ›´æ–°ä¿¡æ¯"Y
ApiCloseWebSocket:DÒóApiCloseWebSocketViewÊó
/close_ws/Úóå…³é—­playerçš„web socket"µ
ApiPresentTargetActor
present_target_actor (&
result
present_target_actor (:VÒóApiPresentTargetActorViewÊó/present-target-actor/Úóè®¾å®šæƒ³è¦çš„é‡‘çš®è§’è‰²"Ì
ApiHomeBackGround
home_back_ground (
	is_random (@
result6
home_back_ground (2.DBGamePlayer.HomeBackGround:HÒóApiHomeBackGroundViewÊó/home-back-ground/Úóè®¾å®šä¸»ç•Œé¢èƒŒæ™¯"Í
ApiGmCachePlayerClean
open_id_start (
open_id_end (
world_id (
result
	clean_cnt (:YÒóApiGmCachePlayerCleanViewÊó/gm-cache-player-clean/Úóæ¸…ç†cacheplayer dbçš„ä½¿ç”¨"ª
ApiChipExchange
chips (D
result:
chip_exchange_info (2.DBGamePlayer.ChipExchangeInfo:BÒóChipExchangeViewÊó/chipexchange/Úóçµå­å…‘æ¢æ´»åŠ¨è®¾ç½®"Ñ
ApiGuildDungeonPlayerRecordQ
resultG
dungeon_records (2..DBGamePlayer.GuildDungeonRecord.DungeonRecord:_ÒóApiGuildDungeonPlayerRecordViewÊó/guild-dungeon-record/Úóå…¬ä¼šå‰¯æœ¬ä¸ªäººè®°å½•æŸ¥è¯¢bMªàzÔâ™ä®÷«N)Iç(Ğ-¶¸›ºØ¨œêà5©	©©j('!Ğšš–¢‚sZ™à¨÷«²*'«^
Xœ6­x`®‹©!Ğ‚º.
î
player_message.protocommon.protooptions.proto"_
DbPlayerMessage

id (
content (	
sender (	

to (	
create_time (":
DbPlayerMessageBoard
	player_id (	
msgs_id ("“
ApiPlayerMessage

to (	
content (	Â
result4
result (2$.ApiPlayerMessage.result.ret_message
ret_message

id (
content (	
sender (	

to (	
create_time (
sender_name (	
to_name (	:œÒóApiPlayerMessageViewÊóX/player-message/<string:player_id>/,/player-message/<string:player_id>/<int:message_id>/Úó$ç©å®¶ç•™è¨€æ¿ï¼Œgetå¯ä»¥åˆ†é¡µæ‹‰bproto3
–
pmhelper.protooptions.proto"3
	DbBugCard
trello_card_id (	
bug_id ("
DbBugCardMap
card_map_id (,
card_map (2.DbBugCardMap.CardMapEntry:
CardMapEntry
key (	
value (2
.DbBugCard:8"Å

ApiBugCard
trello_card_id (	 
post
post (2
.DbBugCard
get
get (2
.DbBugCard'
	get_by_id
result (2
.DbBugCard:4ÒóBugReportViewÊó/BugCard/,/BugCard/<string:id>/"`
ApiQiniuToken
post
token (	
get
token (	:"ÒóQiniuTokenViewÊó/QiniuToken/bproto3
Ö

postcard.protooptions.protocommon.protoxlsx_server.proto"É

DBPostcard
	player_id (	
current_season (
is_first_enter (+
postcard (2.DBPostcard.PostcardEntryƒ
Postcard,
cell (2.DBPostcard.Postcard.CellEntry
prize_receive_status (+
	CellEntry
key (
value (:8E
PostcardEntry
key (#
value (2.DBPostcard.Postcard:8"
ApiPostcardà
result
is_first_enter (7
postcard_info (2 .ApiPostcard.result.PostcardInfo1

prize_info (2.ApiPostcard.result.PrizeInfo¦
PostcardInfo
postcard_id (H
postcard_cell (21.ApiPostcard.result.PostcardInfo.PostcardCellInfo7
PostcardCellInfo
cell_id (

is_lighten ()
	PrizeInfo
prize_receive_status (:›ÒóApiPostcardViewÊó'/Postcard/,/Postcard/<int:postcard_id>/ÚóYè·å–æ˜ä¿¡ç‰‡ä¿¡æ¯(get),å‘ŠçŸ¥serveré¦–æ¬¡è¿›å…¥(post),gmæ¸…ç©ºæ˜ä¿¡ç‰‡ä¿¡æ¯(delete)"ú
ApiPostcardLighten
cell_id (0
result&

res_change (2.STResourceDynamic: ÒóApiPostcardLightenViewÊó$/Postcard/Lighten/<int:postcard_id>/ÚóZç‚¹äº®æ˜ä¿¡ç‰‡(post),gmæ‰¹é‡ç‚¹äº®æ˜ä¿¡ç‰‡(patch),gmé‡ç½®æ˜ä¿¡ç‰‡å•ä¸ªæ ¼å­(delete)"±
ApiPostcardReceive
postcard_id (0
result&

res_change (2.STResourceDynamic:TÒóApiPostcardReceiveViewÊó/Postcard/ReceivePrize/Úóé¢†å–æ˜ä¿¡ç‰‡å¥–åŠ±(post)bproto3
‹
purchase.protocommon.protooptions.proto"¨
DBPurchaseTradeCode
code_id (	
	player_id (	

trade_time (

product_id (	
expire_time (
platform (
channel (
count ("b
DBMidasGoodsOrderRecord
bill_no (	

product_id (	
	player_id (	
open_id (	"‰
DBPurchaseProductBuyRecord
	player_id (	>

buy_record (2*.DBPurchaseProductBuyRecord.BuyRecordEntry=
record_info
	buy_count (
last_buy_start_time (Y
BuyRecordEntry
key (	6
value (2'.DBPurchaseProductBuyRecord.record_info:8"z
DBMidasGoodsOrderToken
order_token (	
	player_id (	

product_id (	
open_id (	
create_time ("Ã
DBPurchaseReceipt
	player_id (	
verifyed (
finished (

trade_code (	
transaction_id (	

product_id (	#
platform (2.PurchasePlatform
channel (
finish_time	 (0

extra_info
 (2.DBPurchaseReceipt.ExtraInfo:
	ExtraInfo
currency (	
money (
coin ("¼
ApiPurchaseDelevery

product_id (	
transaction_id (	

trade_data (	
transaction_receipt (	#
platform (2.PurchasePlatform
google_purchase_data (	
google_signature (	
is_compressed (!
channel	 (2.PurchaseChannel
product_ids
 (	P
result
transaction_id (	
google_purchase_token (	
channel (:JÒóPurchaseDeleveryViewÊó/purchasedelevery/ÚóæœåŠ¡å•†æ”¯ä»˜è¿”å›å€¼"ˆ
ApiPurchaseTouchProduct

product_id (	
is_guest (#
platform (2.PurchasePlatform!
channel (2.PurchaseChannel
count (x
WechatPrepay
	partnerId (	
prepayId (	
nonceStr (	
	timeStamp (	
sign (	
signType (	0
result

trade_data (	

is_sandbox (:DÒóPurchaseTouchViewÊó/purchasetouch/Úóå®¢æˆ·ç«¯ç‚¹å‡»äº†å•†å“"T
ApiMidasDeliverGoods:<ÒóMidasDeliverGoodsViewÊó/midasgoods/Úóç±³å¤§å¸ˆå‘è´§"j
ApiAliPayVerifyPost:SÒóAlipayVerifyViewÊó$/alipayverify/,/alipaysandboxverify/Úóæ”¯ä»˜å®éªŒè¯post"k
ApiWechatPayVerifyPost:QÒóWechatPayVerifyViewÊó"/wxpayverify/,/wxpaysandboxverify/Úóå¾®ä¿¡éªŒè¯postbproto3
º
racepvp.protooptions.proto
xlsx.protofight.protodungeon.protogamedb.protoxlsx_server.protocommon.proto"â
DbRacePVPInfo
	player_id (	
room_uid (
old_room_uid	 (
score (
	old_score (
battle_score (
select_group (
	timestamp
 (
tag (
rewards_recved (
ban_time ("¡
DbRacePVPRoom
uid (

dungeon_id (
round (
	timestamp (/
	room_info (2.DbRacePVPRoom.RoomInfoEntryÈ
PlayerRacePVPInfo
	player_id (	
name (	
battle_score (
	timestamp (
score (
level (

head_photo (
social_picture (	
battle_actors	 (2.STActorQ
RoomInfoEntry
key (	/
value (2 .DbRacePVPRoom.PlayerRacePVPInfo:8"‚
ApiRacePVPInfo±
result

dungeon_id (1
players (2 .DbRacePVPRoom.PlayerRacePVPInfo
	old_score (
score (
is_new (
end_time (
state (
round (:<ÒóRacePVPViewÊó/RacePVPInfo/Úóæ®‹æ¢¦ä¹‹å¢ƒåŸºæœ¬ä¿¡æ¯"œ
ApiRacePVPBattleScoreReward
battle_score (
get
reward_recved (.
post&

res_change (2.STResourceDynamic:˜ÒóRacePVPBattleScoreRewardViewÊó/RacePVPBattleScoreReward/ÚóVæ®‹æ¢¦ä¹‹å¢ƒæˆ˜æ–—ç§¯åˆ†å¥–åŠ±, get--è·å–é¢†å¥–æƒ…å†µï¼Œ post--é¢†å–æŒ‡å®šçš„å¥–åŠ±"
ApiRacePVPScore
score ('
result
seg_id (
score (:DÒóRacePVPScoreViewÊó/RacePVPScore/Úóæ®‹æ¢¦ä¹‹å¢ƒGMä¿®æ”¹ç§¯åˆ†"|
ApiRacePVPSummary
result

reward_tag (:IÒóRacePVPSummaryViewÊó/RacePVPSummary/Úóæ®‹æ¢¦ä¹‹å¢ƒå°çº¢ç‚¹ä¿¡æ¯bproto3
ë
redpoint.protooptions.protocommon.protogamedb.proto
seal.protoachievement.protoracepvp.protodungeon.prototower.protocontinuouslogin.protosignin.protogacha.protoresource.protopostcard.protonotepad.proto
mall.proto
shop.proto"‘

DBRedPoint
	player_id (	4
redpoint_type (2.DBRedPoint.RedpointTypeEntryP

RedPointId
redpoint_type (
redpoint_id (
redpoint_value (˜
RedPointType=
redpoint_id (2(.DBRedPoint.RedPointType.RedpointIdEntryI
RedpointIdEntry
key (%
value (2.DBRedPoint.RedPointId:8M
RedpointTypeEntry
key ('
value (2.DBRedPoint.RedPointType:8"Î	
ApiRedPointData
redpoint_type (
redpoint_id (
redpoint_value (“
result
redpointd (2.DBRedPoint-
seal_flag_result (2.ApiSealFlag.result;
achievement_summary_get (2.ApiAchievementSummary.get:
race_pvp_summary_result (2.ApiRacePVPSummary.resultE
event_dungeon_star_reward_get (2.ApiEventDungeonStarReward.get5
tower_summary_result (2.ApiTowerSummary.result;
continuous_login_result (2.ApiContinuousLogin.result(
signin_result (2.ApiSignin.result)
gacha_info_get (2.ApiGachaInfo.get*
medal_redpoint	 (2.ApiMedal.redpoint,
postcard_result
 (2.ApiPostcard.result7
role_present_redpoint (2.ApiRolePresent.redpoint-
mall_info_result (2.ApiMallInfo.result(
shop_redpoint (2.ApiShop.redpoint3
activity_signin_get (2.ApiActivitySignin.get$
seal_result (2.ApiSeal.result
relation_point_redpoint (:
guild_dungeon_summary (2.ApiGuildDungeonSummary.getC
guild_dungeon_score_reward (2.ApiGuildDungeonScoreReward.getI
guild_dungeon_progress_reward (2".ApiGuildDungeonProgressReward.get%
post
redpoint (2.DBRedPoint::ÒóRedPointDataViewÊó/redpoint/data/Úóå°çº¢ç‚¹æ•°æ®bproto3
Ô/
relation.protocommon.protooptions.proto"•
DBRelationPlayerInfo
	player_id (	4
	base_info (2!.DBRelationPlayerInfo.simple_info1
	page_info (2.DBRelationPlayerInfo.PageInfo+
dun_ass_info (2.DungeonAssistantInfo6
	like_info (2#.DBRelationPlayerInfo.ClickLikeInfo5
recall_info (2 .DBRelationPlayerInfo.RecallInfo9
reported_info (2".DBRelationPlayerInfo.ReportedInfo±
simple_info
name (	
level (
sex (
last_logout_time (

head_photo (
device_type (
social_open_id (	
social_platform	 (
social_picture
 (	
achievement_point (
guild_id (
gamecenter_login_time (
qqvip (
	like_nums (Š
PageInfo
sign (	 
message_board_permission (
aura_actor_uid (
aura_actor_id (
aura_actor_quality (/

show_group (2.DBRelationPlayerInfo.Group
favor_books (
favor_roles (
fight_medal	 (
show_medals
 (û
Group3
members (2".DBRelationPlayerInfo.Group.Member¼
Member
index (
	actor_uid (
actor (2.STActor>
equips (2..DBRelationPlayerInfo.Group.Member.EquipsEntry7
EquipsEntry
key (	
value (2.STEquip:8G
ClickLikeInfo
count (
like_players (	
	timestamp (7

RecallInfo
recall_players (	
	timestamp (i
ReportedInfo
first_reported_time (
reported_score (
guild_id (

ip_address (	"­
DBPlayerOfflineMessage
	player_id (	7
offlines (2%.DBPlayerOfflineMessage.OfflinesEntry-
content
message (	
	send_time (A
offline_info1
msg_info (2.DBPlayerOfflineMessage.contentU
OfflinesEntry
key (	3
value (2$.DBPlayerOfflineMessage.offline_info:8"§
PlayerSummaryInfo
	player_id (	
name (	
level (
sex (
last_logout_time (

head_photo (
device_type (

is_applied (
is_in_relation	 (
social_open_id
 (	
social_platform (
social_picture (	
achievement_point (
sign (	 
message_board_permission (
favor_books (
favor_roles (/

show_group (2.DBRelationPlayerInfo.Group
aura_actor_uid (
aura_actor_id (
aura_actor_quality (
aura_last_used_time (
fight_medal (
show_medals (
guild_id (
gamecenter_login_time (
qqvip (
	like_nums (

all_medals ("Ô
DungeonAssistantInfo
	player_id (	
name (	
level (

head_photo (
last_use_time (
stranger (
aura_actor_id (
aura_actor_quality (
medal	 (
token
 ("ã
DBRelationSocialInfo
	player_id (	<
social_infos (2&.DBRelationSocialInfo.SocialInfosEntry#
social_info
game_players (	U
SocialInfosEntry
key (	0
value (2!.DBRelationSocialInfo.social_info:8"
ApiRelationApplyFriend
	target_id (	,
result"
result (2.PlayerSummaryInfo:4ÒóApplyFriendViewÊó/applyfriend/Úóç”³è¯·å¥½å‹"™
ApiRelationApplyList
	target_id (	
is_agree (
target_list (	 
friend_apply_level_limit ()
get"
result (2.PlayerSummaryInfo*
post"
result (2.PlayerSummaryInfo:NÒóApplyListViewÊó)/applylist/,/applylist/<string:targetId>/Úóç”³è¯·åˆ—è¡¨"•
ApiRelationFriendList)
get"
result (2.PlayerSummaryInfo:QÒóFriendListViewÊó+/friendlist/,/friendlist/<string:targetId>/Úóå¥½å‹åˆ—è¡¨"
ApiRelationSocialFriendList
social_open_id_list (	U
social_player_info
social_open_id (	'
player_info (2.PlayerSummaryInfoH
post@
players (2/.ApiRelationSocialFriendList.social_player_info:@ÒóSocialFriendListViewÊó/socialfriend/Úóç¤¾äº¤å¥½å‹åˆ—è¡¨"Í
ApiRelationBlackList
	target_id (	*
post"
result (2.PlayerSummaryInfo)
get"
result (2.PlayerSummaryInfo:KÒóBlackListViewÊó)/blacklist/,/blacklist/<string:targetId>/Úó	é»‘åå•"’
ApiRelationRecommendFriendList,
result"
result (2.PlayerSummaryInfo:BÒóRecommendFriendListViewÊó/recommendrelation/Úóæ¨èåˆ—è¡¨"È
ApiRelationPlayerPageInfo
name (	
sex (

head_photo (
sign (	 
message_board_permission (/

show_group (2.DBRelationPlayerInfo.Group
favor_books (
favor_roles (
aura_actor_uid (
fight_medal (
show_medals (,
result"
result (2.PlayerSummaryInfo:€ÒóPlayerPageInfoViewÊó4/playerpageinfo/,/playerpageinfo/<string:target_id>/Úó.ç©å®¶ä¸»é¡µä¿¡æ¯,patch åªèƒ½ä¿®æ”¹è‡ªå·±çš„"œ
ApiRelationPlayerSummary,
result"
result (2.PlayerSummaryInfo:RÒóPlayerSummaryViewÊó#/playersummary/<string:id_or_name>/Úóç©å®¶æ‘˜è¦ä¿¡æ¯"‹
ApiRelationRecvRelationPoint©
result
can_recv_cnt (
aura_can_recv_cnt (
get_cnt_this_time (&

res_change (2.STResourceDynamic

point_pool (
today_total_get (:?ÒóPlayerRecvRelationPointViewÊó/relationpoint/Úó	å‹æƒ…ç‚¹"¢
ApiDungeonAssistants
token (/
result%
result (2.DungeonAssistantInfo:JÒóApiDungeonAssistantsViewÊó/dungeon-assistants/ÚóåŠ©æˆ˜å¥½å‹åˆ—è¡¨"o
ApiForceAddFriends
result:OÒóApiForceAddFriendsViewÊó/friends-force/Úóå¼ºè¡Œæ·»åŠ å¥½å‹ï¼Œæµ‹è¯•ç”¨"}
	ApiReport
	player_id (	
reason (
result
	player_id (	:0ÒóApiReportViewÊó/report/Úóä¸¾æŠ¥å…¶ä»–äºº"Ä
ApiPageClickLike
result
	isClicked (:’ÒóApiPageClickLikeViewÊó%/PageClickLikeView/<string:player_id>ÚóMä¸»é¡µç‚¹èµ, get--è·å–æ˜¯å¦å¯¹æ­¤ç©å®¶ç‚¹èµï¼Œ post--å¯¹æ­¤ç©å®¶ç‚¹èµ"µ
ApiFriendRecall
	player_id (	
result
players (	:tÒóApiFriendRecallViewÊó/FriendRecall/ÚóGå¥½å‹å¬å›, get--å·²å‘é€å¬å›è¯·æ±‚çš„ç©å®¶åˆ—è¡¨ï¼Œ post--å¬å›"~
ApiBanRelation
	player_id (	
result:OÒóApiBanRelationViewÊó/relation/ban/Úó#gm banæ‰å¹¿å‘Šå·çš„relationä¿¡æ¯"–
ApiSocialPicVerify
pic_url (	
result
is_valid (:SÒóApiSocialPicVerifyViewÊó/social-pic-verify/Úóç¤¾äº¤å¤´åƒæ˜¯å¦åˆæ³•(post)bproto3
¦
resbuyprice.protooptions.protocommon.proto"å
ApiResBuyPriceT
get
cost (2.STResource
buy (2.STResource
remain_buy_times (:}ÒóResBuyPriceViewÊóV/resbuyprice/<int:resbuyprice_type>/<int:params>/,/resbuyprice/<int:resbuyprice_type>/Úóèµ„æºè´­ä¹°"ƒ
	ApiResBuy
resbuyprice_type (
params (“
result

buy_result (
cost (2.STResource
buy (2.STResource
remain_buy_times (&

res_change (2.STResourceDynamic:6Òó
ResBuyViewÊó/resbuyprice/resbuy/Úóèµ„æºè´­ä¹°"~
ApiTestPushMsg
push_num (
result

buy_result (:<ÒóTestPushMsgViewÊó/resbuyprice/pushmsg/Úóæµ‹è¯•ä»£ç bproì‘
ñ9
resource.protooptions.protocommon.protogamedb.proto
xlsx.proto"×
ApiResourceNewFlag
is_new (-
res_info (2.ApiResourceNewFlag.ResInfo*
ResInfo
	id_or_uid (
type (
patch
patch (:>ÒóApiResourceNewFlagViewÊó
/new-flag/Úóæ–°å¡æ ‡å¿—è®¾ç½®"¦
ApiPlayerBaseResourceFull
count (%
result
result (2.STResource:SÒóPlayerBaseResourceFullViewÊó/fullbaseresource/Úóæ‰€æœ‰åŸºç¡€èµ„æºçš„å¢åŠ "Á
ApiPlayerBaseResourceUpdate»
result&

res_change (2.STResourceDynamicR
last_res_inc_time (27.ApiPlayerBaseResourceUpdate.result.LastResIncTimeEntry5
LastResIncTimeEntry
key (
value (:8:dÒóApiPlayerBaseResourceUpdateViewÊó/baseresource/update/Úó$playerçš„baseresourceæ›´æ–°çš„æ“ä½œ"·
ApiPlayerBaseResource
type (

id (
count (%
result
result (2.STResource:NÒóPlayerBaseResourceViewÊó/baseresource/Úóplayerçš„baseresourceçš„æ“ä½œ"­
ApiResourceCompose
type (

id (
count (0
result&

res_change (2.STResourceDynamic:<ÒóResourceComposeViewÊó/resourcecompose/Úóèµ„æºåˆæˆ"¥
ApiPlayerEmoji

id (
count ("
result
result (2.STEmoji:TÒóPlayerEmojiViewÊó/emoji/Úó2è¡¨æƒ…è·å–ï¼ŒæŒ‰åˆ†é¡µæ‹‰å–emoji, gmæ·»åŠ emoji"¼
ApiPlayerHeadPhoto

id (
count (&
result
result (2.STHeadPhoto:cÒóPlayerHeadPhotoViewÊó/headphoto/Úó9å¤´åƒè·å–ï¼ŒæŒ‰åˆ†é¡µæ‹‰å–headphoto, gmæ·»åŠ headphto"°
ApiPlayerItem

id (
count (!
result
result (2.STItem:aÒóPlayerItemViewÊó/item/,/item/<int:itemId>/Úó-itemè·å–ï¼ŒæŒ‰åˆ†é¡µæ‹‰å–item,gmæ·»åŠ item"Ò
ApiPlayerItemUse

id (
count (
index (`
result&

res_change (2.STResourceDynamic.
profit (2.DBGamePlayer.ProfitDetailInfo:2ÒóPlayerItemUseViewÊó	/itemuse/Úóé“å…·ä½¿ç”¨"À
ApiGmItemAppend&
add_info (2.ApiGmItemAppend.add 
add

id (
count ( 
result
items (2.STItem:AÒóGMAddItemAppendViewÊó/gmitemappend/Úógmæ·»åŠ å¤šä¸ªé“å…·"•
ApiActor

id (
level (
star (&
talents (2.ApiActor.talent_info(
talent_info

id (
value ("
result
result (2.STActor:jÒó	ActorViewÊó/actor/,/actor/<int:actorUid>/Úó7gmæ–°å¢æˆ–è€…åˆ é™¤æˆ–è€…ä¿®æ”¹actor,actorå•ä¸ªè·å–"›
ApiUnlockedActorSkins3
result
unlocked_skins (
	new_skins (:MÒóApiUnlockedActorSkinsViewÊó/unlocked_actor_skins/Úóå·²ç»è§£é”çš®è‚¤"‹
ApiActorSkin

actor_skin (
result

actor_skin (:IÒóApiActorSkinViewÊó/actor/<int:actorUid>/skin/Úóè§’è‰²çš®è‚¤ä¿®æ”¹"Ã
ApiActorLevelUp
food_res (2.STResource.
post&

res_change (2.STResourceDynamic:aÒóActorLevelupViewÊó#/actor/<int:actorUid>/levelupgrade/Úó"actoråå™¬å…¶ä»–actorè¿›è¡Œå‡çº§"¯
ApiActorStarLevelUp.
post&

res_change (2.STResourceDynamic:hÒóActorStarLevelupViewÊó"/actor/<int:actorUid>/starupgrade/Úó&actoråå™¬åŒæ˜Ÿçº§å¡ç‰‡è¿›è¡Œå‡æ˜Ÿ"
ApiActorDecompose
uids (.
post&

res_change (2.STResourceDynamic::ÒóActorDecomposeViewÊó/actor/decompose/Úóactoråˆ†è§£"º
ApiActorInheriting
target_actor_uid (
use_new_talent (£
post&

res_change (2.STResourceDynamic?
equip_change (2).ApiActorInheriting.post.EquipChangeEntry2
EquipChangeEntry
key (
value (:8:LÒóActorInheritingViewÊó!/actor/<int:actorUid>/inheriting/Úóè§’è‰²ç»§æ‰¿"Á
ApiDiffActorInherit
	actor_uid (
target_actor_uid (
use_new_talent (¤
post&

res_change (2.STResourceDynamic@
equip_change (2*.ApiDiffActorInherit.post.EquipChangeEntry2
EquipChangeEntry
key (
value (:8:>ÒóDiffActorInheritViewÊó/DiffActorInherit/Úóè§’è‰²ç»§æ‰¿"Ò
ApiActorSkillLearnning
skill_id (K
post
	new_skill (2.STSkill&

res_change (2.STResourceDynamic:YÒóActorSkillLearnningViewÊó%/actor/<int:actorUid>/skilllearnning/Úóactorå­¦ä¹ æŠ€èƒ½"‹
ApiSetActorLock
is_lock ("
result
result (2.STActor:CÒóActorLockViewÊó/actor/<int:actorUid>/setlock/Úóè§’è‰²ä¸Šé”"©
ApiEquip

id (
level ("
result
result (2.STEquip:^Òó	EquipViewÊó/equip/,/equip/<int:equipUid>/Úó+è·å–å•ä¸ªequip,gmæ·»åŠ æˆ–è€…åˆ é™¤equip"Ü

ApiEquipOn
	equip_uid (
	actor_uid (|
result
	old_actor (2.STActor
	new_actor (2.STActor
	old_equip (2.STEquip
	new_equip (2.STEquip:*ÒóEquipOnViewÊó
/equip/on/Úó	ç©¿è£…å¤‡"±
ApiEquipOnAll

equip_uids (
	actor_uid (C
result
	new_actor (2.STActor

new_equips (2.STEquip:4ÒóEquipOnAllViewÊó/equip/on-all/Úóä¸€é”®è£…å¤‡"¢
ApiEquipCustomMatch
match_index (

equip_uids (Ñ
resultM
equip_custom_match (21.ApiEquipCustomMatch.result.EquipCustomMatchEntry
change_equips (2.STEquipW
EquipCustomMatchEntry
key (-
value (2.DBGamePlayer.EquipCustomMatch:8:ÒóEquipCustomMatchViewÊó/equip/custom-match/ÚóYè‡ªå®šä¹‰è£…å¤‡æ­é…(get-è·å¾—æ‰€æœ‰è£…å¤‡è‡ªå®šä¹‰æ­é…ï¼Œpost-ä¿å­˜è‡ªå®šä¹‰æ­é…)"ç
ApiEquipApplyMatch
match_index (
	actor_uid (J
result
change_actors (2.STActor
change_equips (2.STEquip:]ÒóEquipApplyMatchViewÊó/equip/apply-match/Úó+åº”ç”¨è£…å¤‡æ­é…(post-åº”ç”¨è£…å¤‡æ­é…)"Ö
ApiEquipModifyMatchName
match_index (

match_name (	
result

match_name (	:tÒóEquipModifyMatchNameViewÊó/equip/modify-match-name/Úó7ä¿®æ”¹è£…å¤‡æ­é…åå­—(post-ä¿®æ”¹è£…å¤‡æ­é…åå­—)"Û
ApiEquipOff
body_pos (
	actor_uid (`
result
	new_actor (2.STActor
	new_equip (2.STEquip

off_equips (2.STEquip:EÒóEquipOffViewÊó$/equip/off/,/equip/off/<int:allPos>/Úó	è„±è£…å¤‡"«
ApiEquipEnhance
food_res (2.STResource0
result&

res_change (2.STResourceDynamic:GÒóEquipenhanceViewÊó/equip/<int:equipUid>/enhance/Úóè£…å¤‡å¼ºåŒ–"¯
ApiSetEquipLock
is_lock (Å
result
equip (2.STEquipI
equip_custom_match (2-.ApiSetEquipLock.result.EquipCustomMatchEntryW
EquipCustomMatchEntry
key (-
value (2.DBGamePlayer.EquipCustomMatch:8:CÒóEquipLockViewÊó/equip/<int:equipUid>/setlock/Úóè£…å¤‡ä¸Šé”"—
ApiEquipDecompose

equip_uids (0
result&

res_change (2.STResourceDynamic:<ÒóEquipDecomposeViewÊó/equip/decompose/Úóè£…å¤‡åˆ†è§£"Ã
ApiMedal

id ("
result
result (2.STMedal#
redpoint
medal (2.STMedal:bÒó	MedalViewÊó/medal/,/medal/<int:medalId>/Úó0medalè·å–ï¼ŒæŒ‰åˆ†é¡µæ‹‰å–medal,gmæ·»åŠ medal"Ö
	ApiProfit
	profit_id (
profit_start_time (
profit_end_time (
profit_time (8
result.
result (2.DBGamePlayer.ProfitDetailInfo:3ÒóApiProfitViewÊó/profit/Úóæ”¶ç›Šç±»bufä¿¡æ¯"p
ApiActorFavourFull
result
result (:@ÒóActorFavourFullViewÊó/actor/favourfull/Úóå¥½æ„Ÿåº¦åŠ æ»¡"¿
ApiItemSell'
itemsell (2.ApiItemSell.ItemSell%
ItemSell

id (
count (0
result&

res_change (2.STResourceDynamic:.ÒóItemSellViewÊó
/itemsell/Úóç‰©å“å‡ºå”®"º
ApiActorSkillRead
is_read (
result
is_read (:yÒóActorSkillReadViewÊó./actor/<int:actorUid>/skill-read/<int:skillId>Úó-æŸ¥çœ‹ã€è®¾ç½®è§’è‰²æŠ€èƒ½æ˜¯å¦è¢«æŸ¥çœ‹è¿‡bproto3
Œ	

seal.protooptions.protocommon.protogamedb.proto
xlsx.protoxlsx_server.proto"Ü
DBSeal
	player_id (	
seal (2.DBSeal.Seal¢
Seal
next_refresh_time (1
cell_status (2.DBSeal.Seal.CellStatusEntry5
reward_status (2.DBSeal.Seal.RewardStatusEntry
new_open_flag (
current_season (1
CellStatusEntry
key (
value (:83
RewardStatusEntry
key (
value (:8"•
ApiSealÏ
result+
	cell_info (2.ApiSeal.result.CellInfo/
reward_info (2.ApiSeal.result.RewardInfo1
CellInfo
info (2	.SealCell
fill (4

RewardInfo
info (2.SealReward
got (:8ÒóApiSealViewÊó/seal/Úóè·å–å°ç« ä¿¡æ¯å’ŒçŠ¶æ€"‚
ApiSealFlag
new_open_flag (
result
new_open_flag (:;ÒóApiSealFlagViewÊó/seal/flag/Úóè·å–å°ç« æ ‡å¿—ä½"’
ApiSealFillA
result
cell_id (&

res_change (2.STResourceDynamic:@ÒóApiSealFillViewÊó/seal/fill/<int:cell_id>/Úóå¡«å……å°ç« "¢
ApiSealRewardC
result
	reward_id (&

res_change (2.STResourceDynamic:LÒóApiSealRewardViewÊó/seal/reward/<int:reward_id>/Úóé¢†å–å°ç« å¥–åŠ±
©
share.protocommon.protooptions.proto"l
DbShareInfo
	player_id (	
last_share_time (
weekly_rewarded (
actor_reward_got ("†
ApiShare
activity_id (
	actor_uid (c
result
weekly_rewarded (
actor_reward_got (&

res_change (2.STResourceDynamic:mÒóApiShareViewÊó/share/ÚóNåˆ†äº«å¥–åŠ±ä¿¡æ¯,getæŸ¥è¯¢ç©å®¶çš„åˆ†äº«å¥–åŠ±ä¿¡æ¯,posté¢†å–åˆ†äº«å¥–åŠ±bproto3
Â

shop.protooptions.protocommon.protogamedb.proto
xlsx.protoxlsx_server.proto"è
DBPlayerShop
	player_id (	*
	refreshed (2.DBPlayerShop.Refreshed
trade_count (6
buy_count_record (2.DBPlayerShop.BuyCountRecord-
buffshop (2.DBPlayerShop.BuffshopEntryÿ
	Refreshed/
info (2!.DBPlayerShop.Refreshed.InfoEntryõ
Info2
goods (2#.DBPlayerShop.Refreshed.Info.Single
refresh_time (
refresh_time_free (
refresh_count (
new_shop (
version (Í
Single
position (

shopGoodId (
good (2.STResource
cost (2.STResource
buy_count_record (
	buy_limit (
sold	 (
can_refresh (
guildLevelLimit
 (I
	InfoEntry
key (+
value (2.DBPlayerShop.Refreshed.Info:8Z
BuyCountRecord
shopId (
goodId (
good (2.STResource
count (r
BuffShopInfo4
goods (2%.DBPlayerShop.BuffShopInfo.GoodsEntry,

GoodsEntry
key (
value (:8K
BuffshopEntry
key ()
value (2.DBPlayerShop.BuffShopInfo:8"ƒ
ApiShop
new_shop (Ø
result
	base_info (2	.ShopType
new_shop (
refresh_count (
next_refresh_time (
next_free_refresh_time (&
next_refresh_cost (2.STResource

start_time (
end_time (.
redpoint"
	shop_info (2.ApiShop.result:[ÒóApiShopViewÊó/Shop/,/Shop/<int:shopId>/Úó*å¾—åˆ°å•ä¸ªæˆ–è€…å¤šä¸ªå•†åº—åŸºæœ¬ä¿¡æ¯"Ñ
ApiShopGoods
refresh_free (7
get0
get (2#.DBPlayerShop.Refreshed.Info.Single“
post1
info (2#.DBPlayerShop.Refreshed.Info.Single&

res_change (2.STResourceDynamic
refresh_count (
refresh_time_free (:\ÒóApiShopGoodsViewÊó/Shop/<int:shopId>/Goods/Úó'å¾—åˆ°å•ä¸ªå•†åº—å”®å–çš„å…¨éƒ¨è´§ç‰©"×
ApiShopTrade
	buy_count (
shop_id (1
info (2#.DBPlayerShop.Refreshed.Info.Single
index (v
result
	buy_count (1
info (2#.DBPlayerShop.Refreshed.Info.Single&

res_change (2.STResourceDynamic:iÒóApiShopTradeViewÊó%/ShopTrade/,/ShopTrade/<int:tradeId>/Úó(å•†åº—å•†å“è´­ä¹°postï¼Œäº¤æ˜“æŸ¥è¯¢get"Ñ
ApiActivityBuffShop
goods_id (
grade (j
get5
	shop_info (2".ApiActivityBuffShop.get.GoodsInfo,
	GoodsInfo
goods_id (
grade (.
post&

res_change (2.STResourceDynamic:}ÒóApiActivityBuffShopViewÊó/BuffShop/<int:activity_type>/Úó<post-buffå•†åº—å•†å“è´­ä¹°, get-è·å–å½“å‰çš„è´­ä¹°ä¿¡æ¯"ö
DbPlayerActivityShopGoodsRecord
	player_id (	>
records (2-.DbPlayerActivityShopGoodsRecord.RecordsEntry'
Record

id (
	buy_count (W
RecordsEntry
key (6
value (2'.DbPlayerActivityShopGoodsRecord.Record:8
İ	
signin.protocommon.protooptions.proto"M
DbSignin
	player_id (	
signed (
days (
gift_got ("u
DbActivitySignin
	player_id (	
next_signin_day (
signed (
is_max_signin (
season ("L
DbEveryDaySignin
	player_id (	
signined_days (
season ("Ó
	ApiSignin
days (`
result
signed (
days (
gift_got (&

res_change (2.STResourceDynamic:VÒóApiSigninViewÊó/signin/Úó5ç­¾åˆ°ä¿¡æ¯,postç­¾åˆ°å½“æ—¥,patch,deleteå¯GMä¿®æ”¹"—
ApiSigninGift
days (0
result&

res_change (2.STResourceDynamic:FÒóApiSigninGiftViewÊó/signin/gift/Úóposté¢†å–ç­¾åˆ°ç´¯ç§¯å¥–åŠ±"Š
ApiActivitySignin
days (.
post&

res_change (2.STResourceDynamic7
get
day (
activity_id (
signed (:~ÒóApiActivitySigninViewÊó5/activity-signin/,/activity-signin/<int:activity_id>/Úó(GETæ´»åŠ¨ç­¾åˆ°ä¿¡æ¯ï¼Œpostç­¾åˆ°å½“æ—¥"–
ApiEveryDaySignin
signined_days (
get
signined_days (E
post&

res_change (2.STResourceDynamic
signined_days (:„ÒóApiEveryDaySigninViewÊó6/everyday-signin/, /everyday-signin/<int:activity_id>/Úó-GET æ–°æ¯æ—¥ç­¾åˆ°ä¿¡æ¯ï¼Œ POSTç­¾åˆ°å½“å¤©bproto3
û
$google/protobuf/source_context.protogoogle.protobuf""
SourceContext
	file_name (	B•
com.google.protobufBSourceContextProtoPZAgoogle.golang.org/genproto/protobuf/source_context;source_context¢GPBªGoogle.Protobuf.WellKnownTypesbproto3

google/protobuf/struct.protogoogle.protobuf"„
Struct3
fields (2#.google.protobuf.Struct.FieldsEntryE
FieldsEntry
key (	%
value (2.google.protobuf.Value:8"ê
Value0

null_value (2.google.protobuf.NullValueH 
number_value (H 
string_value (	H 

bool_value (H /
struct_value (2.google.protobuf.StructH 0

list_value (2.google.protobuf.ListValueH B
kind"3
	ListValue&
values (2.google.protobuf.Value*
	NullValue

NULL_VALUE B
com.google.protobufBStructProtoPZ1github.com/golang/protobuf/ptypes/struct;structpbø¢GPBªGoogle.Protobuf.WellKnownTypesbproto3
÷
subscribe.protooptions.protocommon.proto"|
DbSubscribe7
subscribe_msgs (2.DbSubscribe.SubscribeMsgsEntry4
SubscribeMsgsEntry
key (	
value (:8"¸
ApiSubscribe
op_type (0
result
errcode (
is_subscribed (:eÒóApiSubscribeViewÊó/subscribe/<int:platform>Úó0å¾®ä¿¡æˆ–QQæ¨é€è®¢é˜…è®¾ç½® platform:0-wx 1-qq"`
ApiSubscribePush
msg_id (	:<ÒóApiSubscribePushViewÊó/subscribe-push/Úóæ¨é€æ¶ˆæ¯"³
WxSubscribe
ret (
msg (	1
subscribeList (2.WxSubscribe.SubscribeItem
more (I
SubscribeItem

id (
theme (	
status (
title (	"í
QQSubscribe
ecode (
data (2.QQSubscribe.Data

login_cost (
	time_cost (C
Scene
scene_id (	

scene_name (	
is_subscribe (3
	SceneData&

scene_list (2.QQSubscribe.Scenev
RetBody$
data (2.QQSubscribe.SceneData
message (	
	debug_msg (	
result (
	time_cost (m
Key
module (	
method (	
retMsg (	
retCode (%
retBody (2.QQSubscribe.RetBody&
Data
key1 (2.QQSubscribe.Keybproto3
û
switch.protooptions.protocommon.protogamedb.proto
xlsx.proto"«
ApiResAccountSwitch=
result3
switches (2!.DbTempEntranceSwitch.Switch_Info:UÒóApiResAccountSwitchViewÊó/account-switch/Úó"post ä¸Šä¼ jsonä¿®æ”¹AccountSwitchbproto3


task.protooptions.proto
ç
google/protobuf/timestamp.protogoogle.protobuf"+
	Timestamp
seconds (
nanos (B~
com.google.protobufBTimestampProtoPZ+github.com/golang/protobuf/ptypes/timestampø¢GPBªGoogle.Protobuf.WellKnownTypesbu¬¥Šm
»
tournament.protooptions.protocommon.proto
xlsx.protofight.protodungeon.protogamedb.protoxlsx_server.protoasyncpvp.proto"I
DbTournamentConf
next_refresh_time (
current_conf_index ("Á
TotalResult#
mystery_reward (2.STResource
die_num (
win_num (/
win_num_reward (2.TotalResult.win_reward:

win_reward
win_num (
reward (2.STResource"Ú
DbTournament
uid (
	player_id (	
expire_seconds (
battle_room_uid ()
	base_info (2.DbTournament.BaseInfo%
active_info (2.FightActiveData,
balance_info (2.DbDungeon.BalanceInfo"
total_result (2.TotalResult*
	actorInfo (2.DbTournament.ActorInfor
BaseInfo
base_id (
create_time (
	quit_time (
next_refresh_time (

week_index (5
	ActorInfo
	actor_uid (
actor_id_list ("ı
ApiTournamentSummaryR
ActivitiesInfo
activity (2.Activities
status (
	red_point (E
result;
activity_info (2$.ApiTournamentSummary.ActivitiesInfo:JÒóTournamentSummaryViewÊó/tournament/summary/Úóé”¦æ ‡èµ›æ¦‚è¦ä¿¡æ¯"¹
ApiTournamentInfo»
resultA
tournament_info (2(.ApiTournamentInfo.result.TournamentInfo(
tournament_conf (2.TournamentConfD
TournamentInfo
tournament_id (
have_mystery_reward (:fÒóTournamentViewÊó7/tournament/info/<int:activity_type>/<int:activity_id>/Úóé”¦æ ‡èµ›åŸºæœ¬ä¿¡æ¯"
ApiTournamentCost 
get
cost (2.STResource:gÒóTournamentCostViewÊó7/tournament/cost/<int:activity_type>/<int:activity_id>/Úóå–é”¦æ ‡èµ›æ¶ˆè€—"Ê
ApiTournamentEnter
tournament_id (
activity_type (Ä
get
select_actor_group_id (
die_num (
win_num (C
async_pvp_target_info (2$.DBAsyncPvpBattleRecord.BattleRecord7
async_pvp_record (2.ApiAsyncPvpRecordInfo.resultD
post
enter_result (&

res_change (2.STResourceDynamic:yÒóTournamentEnterViewÊóK/tournament/enter/<int:activity_type>/<int:activity_id>/,/tournament/enter/Úóè¿›å…¥é”¦æ ‡èµ›"á
ApiTournamentDungeonEnter
tournament_id (
activity_type (

target_uid (	1
result
dungeon_uid (

dungeon_id (:OÒóTournamentDungeonViewÊó/tournament/dungeonenter/Úóé”¦æ ‡èµ›å‰¯æœ¬è¿›å…¥"¼
ApiTournamentBalance
tournament_id (
activity_type (0
result&

res_change (2.STResourceDynamic:DÒóTournamentBalanceViewÊó/tournament/balance/Úóé”¦æ ‡èµ›ç»“ç®—"Ÿ
ApiTournamentDungeonQuit
activity_type (
result
result (:RÒóTournamentDungeonQuitViewÊó/tournament/dungeonquit/Úóé”¦æ ‡èµ›å‰¯æœ¬é€€å‡º"›
ApiTournamentQuit
tournament_id (
activity_type (
result
result (:>ÒóTournamentQuitViewÊó/tournament/quit/Úóé”¦æ ‡èµ›é€€å‡º"©
ApiTournamentDungeonBalance
activity_type (Y
balance,
balance_info (2.DbDungeon.BalanceInfo
die_num (
win_num (:
post2
post (2$.ApiTournamentDungeonBalance.balance:\ÒóTournamentDungeonBalanceViewÊó/tournament/dungeonbalance/Úóé”¦æ ‡èµ›å‰¯æœ¬ç»“ç®—àó"è
ApiTournamentDungeonActive!
request (2.FightActiveData
result:œÒóTournamentDungeonActiveViewÊó,/tournamentdungeon/<int:dungeon_uid>/active/ÚóIå‰¯æœ¬è¿›è¡Œæƒ…å†µ,åŒ…æ‹¬ç›®æ ‡å®Œæˆæƒ…å†µ,ç›®å‰å¯ä»¥patch active info"˜
ApiTournamentWinReward
tournament_id (
win_num (
activity_type (i
result&

res_change (2.STResourceDynamic7
async_pvp_record (2.ApiAsyncPvpRecordInfo.result:TÒóTournamentWinRewardViewÊó/tournament/winreward/Úóé”¦æ ‡èµ›è·èƒœæ¬¡æ•°å¥–åŠ±bproto3
‹
tower.protocommon.protooptions.proto"¦
DbTower
	player_id (	
locked (
tower_floor (
tower_floor_record (
select_actor_group_id (
unlock_group_id (/
actor_groups
 (2.DbTower.ActorGroupsEntryB
actor_groups_confirmed (2".DbTower.ActorGroupsConfirmedEntry*
enemy_record (2.DbTower.EnemyRecord

reward_got ([
Group&
members (2.DbTower.Group.Member*
Member
index (
	actor_uid (B
ActorGroupsEntry
key (
value (2.DbTower.Group:8»
GroupConfirm-
members (2.DbTower.GroupConfirm.Member
card (
energySp (
energyMp (J
Member
index (
actor (2.STActor

hp (
dead (R
ActorGroupsConfirmedEntry
key ($
value (2.DbTower.GroupConfirm:8'
EnemyRecord

hp (
card ("Ó
ApiTowerActorGroup&
members (2.DbTower.Group.Member:
actor_groups (2$.ApiTowerActorGroup.ActorGroupsEntryB
ActorGroupsEntry
key (
value (2.DbTower.Group:8(
result
result (2.DbTower.Group:kÒóApiTowerActorGroupViewÊó4/tower/actorgroup/,/tower/actorgroup/<int:group_id>/Úóå¾—åˆ°åˆ·å¡”è§’è‰²ç»„"ä
ApiTowerActorGroupConfirmA
actor_groups (2+.ApiTowerActorGroupConfirm.ActorGroupsEntryB
ActorGroupsEntry
key (
value (2.DbTower.Group:8/
result%
result (2.DbTower.GroupConfirm:ÒóApiTowerActorGroupConfirmViewÊóD/tower/actorgroup-confirm/,/tower/actorgroup-confirm/<int:group_id>/Úó!å¾—åˆ°ç¡®è®¤åçš„åˆ·å¡”è§’è‰²ç»„"Ğ
ApiTowerSummary
tower_floor (
tower_floor_record (
floor_reward (­
result
locked (
tower_floor (
tower_floor_record (
select_actor_group_id (
wipe_floor_free (
wipe_floor_pay (

reward_got	 (:FÒóApiTowerSummaryViewÊó/tower/summary/Úóå¾—åˆ°åˆ·å¡”åŸºæœ¬ä¿¡æ¯"¼
ApiSelectedActorGroupTower
select_actor_group_id ('
result
select_actor_group_id (:VÒóApiSelectedActorGroupTowerViewÊó/SelectedGroupId/tower/Úóåˆ·å¡”é»˜è®¤è§’è‰²ç»„" 
ApiTowerWipe
wipe_to_floor (E
result
tower_floor (&

res_change (2.STResourceDynamic:2ÒóApiTowerWipeViewÊó/tower/wipe/Úó
postæ‰«è¡"»
ApiTowerFloorReward
floor_reward (D
result&

res_change (2.STResourceDynamic

reward_got (:HÒóApiTowerFloorRewardViewÊó/tower/floor-reward/Úópost è·å–å¥–åŠ±bproto3
ó
triggered_event.protooptions.protocommon.proto
xlsx.protogamedb.protofight.proto"Ú
DBTriggeredEvent
	player_id (	"
last_active_triggered_time (#
last_passive_triggered_time ($
daily_triggered_event_finish (4

event_info (2 .DBTriggeredEvent.EventInfoEntry¾
	EventInfo
event_id (

event_type (
	sender_id (	
create_time (
	last_time (
friend_send (
	event_key (	
event_level (
sender_name	 (	M
EventInfoEntry
key (	*
value (2.DBTriggeredEvent.EventInfo:8"d
DBTriggeredEventOfflineMessage
	player_id (	/

event_list (2.DBTriggeredEvent.EventInfo"Ê
ApiTriggeredEvent
	ticket_id („
get/

event_info (2.DBTriggeredEvent.EventInfo$
daily_triggered_event_finish (&
display_triggered_event_ticket (­
post/

event_info (2.DBTriggeredEvent.EventInfo$
daily_triggered_event_finish (&
display_triggered_event_ticket (&

res_change (2.STResourceDynamic:kÒóApiTriggeredEventViewÊó/triggered-event/Úó9è§¦å‘å‹äº‹ä»¶æŸ¥è¯¢,åˆ›å»ºè§¦å‘å‹äº‹ä»¶(æ¶ˆè€—é—¨ç¥¨)bproto3
Ò
	tss.protooptions.protocommon.protogamedb.proto
xlsx.protoxlsx_server.proto"s
ApiTSSAntiReport
data (:QÒóApiTSSAntiReportViewÊó
/tss/anti/Úó#TSS Anti Report(TSSåå¤–æŒ‚ä¸ŠæŠ¥)àóbproto3
‹
tutorial.protooptions.proto
xlsx.proto"Õ
ApiTutorial
status (
step_force_finish (#
result
result (2	.Tutorial:vÒóTutorialViewÊóR/tutorial/,/tutorial/<int:tutorial_id>/,/tutorial/<int:tutorial_id>/<int:step_id>/Úóæ–°æ‰‹å¼•å¯¼bproto3
º
google/protobuf/type.protogoogle.protobufgoogle/protobuf/any.proto$google/protobuf/source_context.proto"×
Type
name (	&
fields (2.google.protobuf.Field
oneofs (	(
options (2.google.protobuf.Option6
source_context (2.google.protobuf.SourceContext'
syntax (2.google.protobuf.Syntax"Õ
Field)
kind (2.google.protobuf.Field.Kind7
cardinality (2".google.protobuf.Field.Cardinality
number (
name (	
type_url (	
oneof_index (
packed ((
options	 (2.google.protobuf.Option
	json_name
 (	
default_value (	"È
Kind
TYPE_UNKNOWN 
TYPE_DOUBLE

TYPE_FLOAT

TYPE_INT64
TYPE_UINT64

TYPE_INT32
TYPE_FIXED64
TYPE_FIXED32
	TYPE_BOOL
TYPE_STRING	

TYPE_GROUP

TYPE_MESSAGE

TYPE_BYTES
TYPE_UINT32
	TYPE_ENUM
TYPE_SFIXED32
TYPE_SFIXED64
TYPE_SINT32
TYPE_SINT64"t
Cardinality
CARDINALITY_UNKNOWN 
CARDINALITY_OPTIONAL
CARDINALITY_REQUIRED
CARDINALITY_REPEATED"Î
Enum
name (	-
	enumvalue (2.google.protobuf.EnumValue(
options (2.google.protobuf.Option6
source_context (2.google.protobuf.SourceContext'
syntax (2.google.protobuf.Syntax"S
	EnumValue
name (	
number ((
options (2.google.protobuf.Option";
Option
name (	#
value (2.google.protobuf.Any*.
Syntax
SYNTAX_PROTO2 
SYNTAX_PROTO3B}
com.google.protobufB	TypeProtoPZ/google.golang.org/genproto/protobuf/ptype;ptypeø¢GPBªGoogle.Protobuf.WellKnownTypesbproto3
•!

vote.protooptions.protocommon.protogamedb.proto
xlsx.protoxlsx_server.proto"³

DbVoteRole
vote_term_id (	7
role_vote_count (2.DbVoteRole.RoleVoteCountEntry
total_vote_count (H
total_vote_reward_record (2&.DbVoteRole.TotalVoteRewardRecordEntry4
RoleVoteCountEntry
key (
value (:8<
TotalVoteRewardRecordEntry
key (
value (:8")
DbPlayerVoteRoleRecord
role_id ("È
ApiVoteRole
role_id (

vote_count (¿
result
activity_time_start (
activity_time_end (
left_vote_count (
total_vote_count (/
	role_info (2.ApiVoteRole.result.RoleInfoR
vote_role_target_reward_info (2,.ApiVoteRole.result.VoteRoleTargetRewardInfoC
reward_localization (2&.ApiVoteRole.result.RewardLocalization 
vote_reward (2.STResource
vote_enable	 (
left_vote_bang_count
 ( 
exchange_vote_bang_count (
exchange_vote_count (<
RoleInfo
role_id (

vote_count (
url (	Z
VoteRoleTargetRewardInfo,
target_reward (2.VoteRoleTargetReward
finished (R
RewardLocalization
type (

id (
	type_name (	
id_name (	:RÒóApiVoteRoleViewÊó%/vote/role/,/vote/role/<int:role_id>/Úóè§’è‰²æŠ•ç¥¨åº”æ´"h
DbPlayerWebSigninRecord
	player_id (	
days (
signin_timestamp (

is_sign_in ("G
DbWebSigninGlobalRecord
sign_in_count (
reward_record ("ñ
ApiWebSigninÌ
get1
sign_in_rewards (2.ApiWebSignin.get.Reward
global_sign_in_count (C
global_target_rewards (2$.ApiWebSignin.get.GlobalTargetReward

is_sign_in (1
Reward
type (

id (
count (h
GlobalTargetReward
sign_in_count (
finished ()
rewards (2.ApiWebSignin.get.RewardA
post
result ()
rewards (2.ApiWebSignin.get.Reward:OÒóApiWebSigninViewÊó/WebSignin/Úó(get-ç½‘é¡µç­¾åˆ°æ´»åŠ¨è¯¦æƒ… post-ç­¾åˆ°"Ë
DbPlayerWebVoteRecord
	player_id (	7
	vote_list (2$.DbPlayerWebVoteRecord.VoteListEntry@
more_vote_list (2(.DbPlayerWebVoteRecord.MoreVoteListEntry
reward_record (

vote_count (I
RoleVoteInfo

vote_count (
vote_timestamp (
image (	T
VoteListEntry
key (2
value (2#.DbPlayerWebVoteRecord.RoleVoteInfo:8X
MoreVoteListEntry
key (	2
value (2#.DbPlayerWebVoteRecord.RoleVoteInfo:8":
DbWebVoteRoleRecord
role_id (

vote_count ("Ô

ApiWebVote
role_id (
	role_name (	

vote_count (‘
get+
	role_list (2.ApiWebVote.get.RoleInfo0
more_role_list (2.ApiWebVote.get.RoleInfo=
vote_target_rewards (2 .ApiWebVote.get.VoteTargetReward-
	vote_list (2.ApiWebVote.get.MyVoteInfo

vote_count (
vote_bang_num (
vote_ticket_num (
exchange_vote_bang_num ( 
exchange_vote_ticket_num (1
Reward
type (

id (
count (ƒ
RoleInfo
role_id (
	role_name (	
image (	

vote_count (
sort_id (
novel (	
novel_id (a
VoteTargetReward

vote_count (
finished ('
rewards (2.ApiWebVote.get.Rewardš

MyVoteInfo
role_id (

vote_count (
vote_timestamp (
	role_name (	
image (	
type (
novel (	
novel_id (·
post
result ('
rewards (2.ApiWebVote.get.Reward2
vote_role_info (2.ApiWebVote.get.MyVoteInfo

vote_count (
vote_ticket_num (
reward_record (:@ÒóApiWebVoteViewÊó	/WebVote/Úóget-æŠ•ç¥¨è¯¦æƒ… post-æŠ•ç¥¨ "Ñ
ApiWebVoteSummaryø
get2
	role_list (2.ApiWebVoteSummary.get.RoleInfo7
more_role_list (2.ApiWebVoteSummary.get.RoleInfoƒ
RoleInfo
role_id (
	role_name (	
image (	

vote_count (
sort_id (
novel (	
novel_id (:AÒóApiWebVoteSummaryViewÊó/WebVoteSummary/Úóget-æŠ•ç¥¨è¯¦æƒ…"´
ApiWebVoteUploadImage
role_id (
	role_name (	
image (	
post
result (:PÒóApiWebVoteUploadImageViewÊó/WebVoteUploadImage/Úópost-ä¸Šä¼ è§’è‰²å›¾ç‰‡"m
ApiWebActivity
get

activities (:@ÒóApiWebActivityViewÊó/WebActivity/Úóget-å½“å‰æ´»åŠ¨çš„idbV‹^Mêæ!Ôh•åhµà¨
¿
google/protobuf/wrappers.protogoogle.protobuf"
DoubleValue
value ("

FloatValue
value ("

Int64Value
value ("
UInt64Value
value ("

Int32Value
value ("
UInt32Value
value ("
	BoolValue
value ("
StringValue
value (	"

BytesValue
value (B|
com.google.protobufBWrappersProtoPZ*github.com/golang/protobuf/ptypes/wrappersø¢GPBªGoogle.Protobuf.WellKnownTypesbproto3
¹¬

xlsx.protoconfig.protooptions.protocommon.proto">
SingleActorGroup
groupID (Bˆ¦
groupmember ("h
	ActorInfo
id (Bˆ¦
quality (Bˆ¦
attrs (2.STAttr
quality_attr_ratio ("Ä
ActorConfig
id (Bˆ¦
openTime  (	
openTimeStamp! (
burstLinkExclude (
role (
kind (
animType (
animRes (	
logicRes (	
	prefabRes (	
width (
height (
hit_w	 (
hit_h
 (
scale (
close (
shadowBaseY (
shadowOffset (
poseActionCnt (
chooseAudio (	

hookObject (	
book (
autoGenSkillhit (
showInLogin (
exclusiveTalentId (
portraitOffsetX (
portraitOffsetY (
logoOffsetX (
logoOffsetY (
supSound (	
voiceRes" (	
portraitUVOffsetX# (
portraitUVOffsetY$ (
enableStartVoice% (
enableGameStartVoice& (
category' (
shareGachaResultOffsetX( (
	skinUIRes) (
	isLimited* (

activityId+ (:¦"…
BulletConfig
id (Bˆ¦
name (	
res (	
logic (	
r_type (
r_attack (
	r_injured (:¦"[
ActionDamage
actor_id (
skill_id (Bˆ¦
name (	Bˆ¦
index ("É
DamageIndex
id (Bˆ¦
level (Bˆ¦
maxLevel (
attrs (2.STAttr
hpType (
hpVal (
upRate (

addAttType (

addAttKind	 (

addAttRate
 ("Ğ
DamageIndexMonster
id (Bˆ¦
level (Bˆ¦
maxLevel (
attrs (2.STAttr
hpType (
hpVal (
upRate (

addAttType (

addAttKind	 (

addAttRate
 ("

ActionBuff
actor_id (
skill_id (Bˆ¦
name (	Bˆ¦
	addroleid (Bˆ¦
	logicname (	Bˆ¦
	buffIndex ("F
SkillCamera
skill_id (Bˆ¦
info (2.CameraInfo:¦"ˆ

CameraInfo
time (
fov (
x_per (
x_val (
y_per (
y_val (
actor (
single (:ˆ¦"˜

ActorSkill
skill_id (Bˆ¦
actor_id (
name (	

cost_index (
follow_skill (
cmdStr (	
kind (
defense (	
target	 (
specialIndex
 (
display_index (
is_link (

is_passive (
	res_index (:¦"
	ActorLink
linkID (Bˆ¦
guestActorIds (
link (2	.LinkInfo
autoGenSkillhit (
kindIcon (	
cost_mp (
cost_sp (
	fireCount	 (
actorRun
 (

attackType (	
attackTargetsCount (
volumVal (
openTime (	:¦"A
LinkInfo
actorID (
skillID (
delay (:ˆ¦"

ActorCombo
actor_id (Bˆ¦

card_count (Bˆ¦
s_count (Bˆ¦

skill_list (
cost_mp (
cost_sp (
kindIcon (	

attackType (	
attackTargetsCount	 (
volumVal
 (
	fireCount (

placeIndex (
paramInt (:¦"¸
SkillHit
id (Bˆ¦
card (Bˆ¦
s_card (Bˆ¦
	self_seat (Bˆ¦

team_type1 (Bˆ¦

team_type2 (Bˆ¦
hit_list (	
	cost_time ("«
SkillHitIndex
id (Bˆ¦
card (Bˆ¦
s_card (Bˆ¦
	self_seat (

team_type1 (

team_type2 (
hit_list (	
	cost_time ("0
TeamPVETalk
id (Bˆ¦
talktag (	"û
CartoonConfig
id (Bˆ¦
	chapterId (
	dungeonId (
res (	
winPlay (
roleID (

movie_icon (	
eventActivityId (
isOp	 (
order
 (
	isSubPlot (
pvName (	
	storyType (:¦"Q
CartoonActorName
id (Bˆ¦

emoteActor (	
	emoteName (	:¦"p
	AffixInfo
id (Bˆ¦
index (
level (
kind (

affixTitle (	
	affixDesc (	"—
DungeonAffix
	dungeonId (Bˆ¦&
team1 (2.DungeonAffix.AffixList&
team2 (2.DungeonAffix.AffixList
	AffixList
	affixList ("Û
DungeonModeIndex
id (Bˆ¦
time (
maxSp (
maxMp (
initSp (
initMp (

changeRate (
addSp (
addMp	 (
roundSp
 (
roundMp (
infiniteSkill ("Î
DungeonMonster
	dungeonID (Bˆ¦
stageID (	
musicID (	

aiFilename (	
	firstTeam (

team1Affix (

team2Affix (
	monsterId (
level	 (
levelUpIndex (
showType
 (
showDesc (

failedDesc (
winParam (
	stageIcon (
hintShow (
	modeIndex (
recommendID (
recommendDesc (

isShowHelp (
bornAnim ( 
showHintLoseNumThreshold (:¦"À
MonsterInfo
	monsterID (Bˆ¦
actorID (
closeID (
monsterType (
levelUpIndex (
	buffIndex (
specialIndex (
special (2.TalentInfo:¦"P
MonsterLevelUpBase
level (Bˆ¦
attrs (2.STAttr
sword ("B
MonsterLevelUpIndex
index (Bˆ¦
attrs (2.STAttr"O
GameModeInfo
gameMode (Bˆ¦'
enterCondition (2.EnterCondition"ş
DungeonReward
	dungeonId (Bˆ¦
type (
difficult_level_low (
difficult_level_high (

enterTimes (
preDungeonId (
cost (2.STResource
cost_win (2.STResource 
dropDisplay (2.STResource
dropId (	
dropIdFirst (	
dropIdMultiple (	5
profitDropInfo (2.DungeonReward.ProfitDropinfo%
target (2.DungeonReward.Target
onlyOnce	 (
randomDungeon
 ("
randomDungeonUnlockSeconds (
expActor (
expAssistant (
unlock_level (
	difficult (
notFight (2
addDrop (2!.DungeonReward.AddtionalStateInfo%
dropDisplayFirst (2.STResource'
dropDisplayOutside (2.STResource'
enterCondition (2.EnterCondition
expectCulDegree (
triggered_event_pool (
display_type (
chipExchangePlayerPos (
chipExchangeDropCount  (
preDungeonDoneTimes! (
dungeonGroups" (

chapter_idé (
key_dungeonê (1
Target

id (
type (
param (9
ProfitDropinfo
	profit_id (
dropIdProfit (	5
AddtionalStateInfo
stateId (
dropId (	"n
StoryDungeon
	dungeonId (Bˆ¦
	monsterId (
level (
levelUpIndex (
guide ("„
ActivityDungeon
activity_id (Bˆ¦

dungeon_id (
multiple (
multipleDay (
multipleActivity_id (">
ActivitySort
	sort_type (Bˆ¦
activity_type ("”
ChapterInfo
	chapterId (Bˆ¦
preChapterId (
postChapterId	 (
type (
actorId (
chapterIndex
 (
	dungeonId (9
randomDungeonInfo (2.ChapterInfo.RandomDungeonInfo
maxRandomDungeon (
storyChapterId (

trainingId (
preChapterStar (
dungeonTypeStar (
keyDungeonId (/
RandomDungeonInfo

id (
chance ("‹
ChapterReward
	chapterId (Bˆ¦D
star (B6ˆ¦Òä.0è¡¨ç¤ºé€šå…³å¥–åŠ±ï¼Œå…¶ä»–è¡¨ç¤ºæ˜Ÿçº§å¥–åŠ±
reward (2.STResource"“
DungeonSlotBackground
	dungeonId (Bˆ¦
activity_id (Bˆ¦
slotBackground (	
dropDisplayType (
dropDisplayId ("…
SpecialAttrInfo
index (Bˆ¦
maxLevel (
timing (
ownerIdentityCategory (
targetIdentityCategory (
targetAttackType (
targetIsAllOrOne (
childSpecialAttrInfoId (9
probabilityInfo	 (2 .SpecialAttrInfo.ProbabilityInfo.

conditions
 (2.SpecialAttrInfo.Condition
score (
scoreMax (¹
ProbabilityInfo
checkOnceOrEach ( 
isStaticOrBaseOnCardInfo (
staticProbability (
bigSkillProbability (
linkProbability (
cardNumProbability (ß
	Condition
type (
sourceIdentityCategory (
sourceAttackType (
sourceIsAllOrOne (
sourceExtraParam (
targetValue (
	operation (
sourceOperation (	
sourceOperand	 ("R
SpecialLevelUpIndex
index (Bˆ¦
attrs (2.STAttr
upRate ("‘
BuffData
id (Bˆ¦
owner  (Bˆ¦
priority! (
maxLevel (
upRate (
	lastRound (
effect (
type (
maxRound (
maxStack (
endPoint (
paramInt (
paramStr (	
categoryList (
replace	 (
	hitEffect
 (	
hitBind (
hitX (
hitY (
hitDelay (

loopEffect (	
loopX (
loopY (
	loopLayer (
hideFlag (
iconName (	
intro (	
describe (	
ignoreAttacker" (:¦"“
	BuffIndex
index (Bˆ¦
describe (	
isChanceIndependent (
merge (
buffId (
owner (
launchChance ("F
PresetSettings
level (Bˆ¦
settings (2.GameSettings"–
PlayerLevelInitConfig
level (Bˆ¦
exp (
unlock_mode (5
inc_info (2#.PlayerLevelInitConfig.res_inc_info"
maxRecvRelationPointOneDay (
unlock_window (!
level_reward (2.STResource
next_unlock_window	 (
equipLevelLimit
 (
towerTeamNum (f
res_inc_info
type (

id (
max (
inc (
inc_seconds (
limit ("=
ActorStar2LevelLimit
star (Bˆ¦
	max_level ("`
ActorLevelup
level (Bˆ¦
exp (
exp_lost_rate (
equip_cul_ratio ("
ItemInfo
id (Bˆ¦
icon (	
desc (	
quality (
	func_type (
param (
type (

broad_type	 ('
floor_score (2.ResDropFloorScore
sources (
dropIds (	
maxUseCount (
expire (
saleable (
	autoTrade (
	sellPrice (2.STResource
drop_max_count (
bind_activity_type (
bind_activity_id ("e
	ManualBox

id (
type (
in_box (2.STResource!
in_box_array (2.STResource"H
ActorAttrLevelPromotion
actorId (Bˆ¦
attrs (2.STAttr"½
ActorAttrStarPromotion
actorId (Bˆ¦
star (Bˆ¦

levelLimit (
attrs (2.STAttr
	star_cost (2.STResource
starCulDegree (
unlockSkillId ("™&
Misc
defaultLanguage (
maxCardGroupNum ('
equipExpToResource (2.STResource
dailyRefreshTime (	
minAnyPurchase (
maxAnyPurchase (
danMuIntervalSeconds	 (
maxSkillPoint
 (
maxRecentContact ("
maxWorldSubChannelCapacity (
maxPlayerMail (
maxGlobalMail ( 
maxWorldSubChannelWeight (
maxActorLevel (
maxActorGroup (
maxGoldActorGroup (
maxWorldSubChannelCount (
maxOfflineChatNum (
maxTowerActorGroup (
towerShowNextAwardCount (
talent_ratio (
maxNotifyDeployNum (
maxAccomplishDeployNum (
resetTalentIndexPos (
percentAttrNum (
spaceBooksCount (
playerHeadCount ( 
actorDecomposeTalentCoef  ("
maxActorCommentCountOneDay! (
auraCooldown" ($
auraRelationPointAddStranger# (#
auraRelationPointAddFriends$ (
maxUnlockPosterScore% (#
gachaRecordActorTalentScore& (
gachaRecordActorMax' ( 
mailAttachmentLevelLimit( (
oneStarRewardCount) (
dungeonBalanceFavour* (
signboardFavourCycle+ (
signboardFavourValue, (
deployFavourCycle- (
deployFavourValue. (
maxRoleFavourLevel/ (
firstGetRoleFavour0 (
gachaMaxCountOnce1 (
gachaPointGenRation2 (
monthCardIncreaseDays3 ()
monthCardRewardDaily5 (2.STResource
maxStarLevel6 (

pvpTickets7 (
pvpOneRandActorNum8 (
pvpRandTimes9 (
pvpMinRandTime: (
pvpMaxRandTime; (
pvpMaxWinNum< (
pvpMaxDieNum= (
maxCongratsRoleNum> (
minCongratsFavourLv? (
rolePresentInterval@ (
rolePresentMaxNumA (
racePvpMinRandTimeB (
racePvpMaxRandTimeC (
maxRacePvpPlyNumD (
burstPvpActorStarE (
burstPvpActorLevelF (
burstPvpActorQualityG (
maxAsyncPVPActorGroupO (
maxRacePVPActorGroupP ("
notepadEventVoiceOpenLevelQ ( 
burstPvpMatchMaxWaitTimeR (
unlockNoticeSystemLevelS (

maxNameLenT (

maxChatLenV (
gameCenterDSRewardTypeW (
gameCenterDSRewardCountX (
recallSinceLastLoginY (
vipSuperDSRewardTypeZ (
vipSuperDSRewardCount[ (
maxFriendNum\ (
maxBlackContactNum] (
maxFriendApplyListNum^ (
maxActorCommentCount_ (
maxResetTalentCount` ( 
fightInteractionIntervala (
racePVPGroupMaxPlayerb (
clickLikeLimitc (
dailyRefreshZeroTimed (	
reportedRefreshTimee (
reportedGuildNumf (
maxReportedScoreg (
chatBanColdTimeh (
reportedIpLimiti (
contractUrlj (	
forceLimitedPoolCountk (
racePVPTicketNuml (
racePVPRoundNumm (&
teamDungeonTicketn (2.STResource
wxSubscribeMsgIdo (
qqSubscribeSceneIdp (
relationPointDailyLimitq ($
triggeredEventActiveIntervalr (%
triggeredEventPassiveIntervals (
triggeredEventTicketst ('
dayTriggeredEventActiveOpenTimeu (	(
 dayTriggeredEventActiveCloseTimev (	(
 dayTriggeredEventPassiveOpenTimew (	)
!dayTriggeredEventPassiveCloseTimex (	
triggeredEventLastTimey ($
dayTriggeredEventFinishLimitz (%
dayPassiveTriggeredEventLimit{ (#
displayTriggeredEventTicket| (*
"maxTriggeredEventOfflineMessageNum} (#
channels~ (2.Misc.ChannelInfo&
gameDataReportOnlineTimePeriod ()
 gameDataReportPurchaseTimePeriod€ ("
notifyCD (2.Misc.NotifyCDs
maxActorWarningCount‚ (
maxEquipWarningCountƒ ( 
dailySubscribeCheckTime„ (	
payTestRechargeEndTime† (	
payTestRewardStartTime‡ (	
payTestDisableˆ (
maxDanmuVideoLengthŠ (
maxDanmuTextLength‹ (
msdkFriendIconŒ (	 
modifyNameTutorialLevel (
unlockFightSpeedLevel (
taskPresentRewardBoxId (
gachaVerify ("
CreditsListScrollVelocity‘ (
citySceneUpdateTime1’ (
citySceneUpdateTime2“ (#
clawCraneCost” (2.STResource
clawCraneSupplyTime• (	"
maxTriggeredEventOwnCount– (
specialActivityId— (
gachaFreeTime˜ (	"
clawCraneTotalProbability™ (!
gachaPurpleSameTypeCountš (!
gachaOrangeSameTypeCount› (%
normalGachaPurpleProbabilityœ ($
limitGachaPurpleProbability (%
gachaForceOrangeProbabilitiy (
actorAttrsVersion  (
friendApplyLevelMin¡ (
friendApplyLevelMax¢ (
mailWarningCount£ (
chipExchangeSet¤ (#
chipExchangeMaxModifyCount¥ ( 
dungeonActorOverflowExp¦ (
purblePairsTotalCnt§ (
purblePairsPerGroupCnt¨ ( 
purblePairsMaxCntPerDay© ( 
cultivationPVPHideRanksª (
guildDungeonCloseTime« (	
guildDungeonOpenTime¬ (	
talentCheckVersion­ (A
ChannelInfo
name (	

id (	
privilege_ignore (4
	NotifyCDs
	powerFull (
deployFinish ("@
AttrTypeSortOrder
list (
ActorMaxLevelShowList ("†
	EquipInfo
equipId (Bˆ¦
icon (	
descId (	
quality (
pos (
score (

baseAttrId (

randAttrId (
specialAttrId	 (
decomposeDropId
 (	
suitId (

broad_type (
kind ('
floor_score (2.ResDropFloorScore
sources (
base_exp (
require_level (
collectionsIndex (
openTime (	"±
EquipEnhance
quality (Bˆ¦
level (Bˆ¦

levelUpExp (
enhanceLostRate (
genNewRandAttr (
qualityCulDegree (
levelCulDegree ("M
EquipBaseAttr
id (Bˆ¦
level (Bˆ¦
attr (2.STAttr"¶
EquipSuitAttr
id (Bˆ¦
equipId (%
suitAttr (2.EquipSuitAttr.suit
collectionsIndex (A
suit
	suitCount (
specialAttrId (
suitCul ("Œ
EquipRandomAttr
id (Bˆ¦.
	rangeMain (2.EquipRandomAttr.attr_range-
rangeSub (2.EquipRandomAttr.attr_range1
rangeSpecial (2.EquipRandomAttr.attr_range0
range_array (2.EquipRandomAttr.attr_rangeU
	attr_info
type (
isRatio (
min (
max (
segment (L

attr_range)
attrs (2.EquipRandomAttr.attr_info
probability ("o
SkillLevelup
	costIndex (Bˆ¦
level (Bˆ¦
cost (2.STResource
levelCulDegree ("C
BoardActorAction
name (	
audio (	
text (	:ˆ¦"«
BoardActorConfig
id (Bˆ¦
name (	
emote (	
offsetX (
offsetY (
scale (

rangeEyeLR (

rangeEyeUD (
rangeHeadLR	 (
rangeHeadUD
 (
rangeBodyLR (
rangeNoseLR (
appearanceCount ("
actions (2.BoardActorAction"¥
SceneConfig
	sceneName (	Bˆ¦
fixedFrameRate (
detailLoading (
keepDeviceAwake (
fadeWhenExit (
fadeWhenEnter (:¦˜¦"¬
PurchaseInfo

product_id (	Bˆ¦
product_name (	
product_picture (	
price (
count (
promotion_cnt (
show_in_shop (
ratio (
platform	 (
maxBuyCount
 (
maxCountClearInterval (!
purchase_res (2.STResource
channels ("©
ChatChannel
channel (Bˆ¦
interval (

levelLimit (
cost (2.STResource
clear_over_time (
max_get_num (
max_show_num ("‹
	ChatImage
type (Bˆ¦
id (Bˆ¦
atlas (	
sprite (	
imageRoleId (
text (	
canUseForFight ("g
PlayerHeadPhoto
id (Bˆ¦
atlas (	
sprite (	
imageRoleId (
getWay ("<
Goto
id (Bˆ¦

windowName (	
params (	"O
ResourceQuality
resourceType (Bˆ¦
quality (
sources ("ı
AchievementInfo
id (Bˆ¦
pre_id (
unlock_level (
hide (
category (
priority (
	goal_type (
max_progress (
param	 (
reward
 (2.STResource
display_role_id (

broad_type ("e
AchievementPointTarget
trophy (Bˆ¦
level (Bˆ¦ 
target_achievement_point ("†
	GachaType
id (Bˆ¦
type (
cost (2.STResource
costSub (2.STResource
	safeCount (
maxCountOnce (
dropIdForce (	
dropId (	
dropIdByScore (	
dropIdNewActor (	
hide	 (
maxCountPerDay (
drop_floor_channel (

windowName (	
gachaFreeInterval (
actor_drop_count (
purple_actor_param (
orange_actor_param ( 
limit_orange_probability (
not_get_orange_decrease (
not_get_orange_min (
not_get_orange_max (#
limit_convention_drop_param  ("@
Type

Normal 
	ActorChip
	TimeLimit

Friendship"Q
GachaActorChipChance
id (Bˆ¦
	remainder (Bˆ¦
chance ("7
GachaActorPool
actorId (Bˆ¦
weight ("S
GachaActorChipWeight
chipId (Bˆ¦
actorId (Bˆ¦
weight ("Ü
ActivityGachaNormalUp
activity_id (Bˆ¦
probability (6
drop_res_info (2.ActivityGachaNormalUp.DropInfo
drop_actor_count (A
DropInfo
drop_actor_id (
drop_actor_probability ("â
ActivityGachaLimited
activity_id (Bˆ¦
probability (5
drop_res_info (2.ActivityGachaLimited.DropInfo
drop_actor_count (I
DropInfo
drop_res (2.STResource
drop_actor_probability ("‚
GachaAd
activity_id (Bˆ¦
displayName (	
order (
window (
emote_id (
jumpActivityId ("Ó
	GuildMisc
maxMemberCount (

maxNameLen (
maxBillboardLen (
createMinLevel (

createCost (2.STResource
	viceCount (
randomBatchCount (
love_score_mu	 (
love_score_sigma
 (
	typeCount (

badgeCount (

renameCost (
editIconCost (
entryCD (
donateMaxCount (!
donateMaxCountByOnePlayer (
maxGuildDeployDoneCount (1
love_segment (2.GuildMisc.LoveScoreSegment
	maxRecord (
voteStartPresidentLeave (
voteLastTime (
voteCoolDown (!
donateRefund (2.STResource
maxLoveScore (
donateRefreshIsoWeekday (
maxGuildPendingList ("
guildFunctionLimitWhenQuit (
punishQuitCount  (
maxGuildLevel! (N
LoveScoreSegment
	score_low (

random_low (
random_high ("`

GuildLevel
level (Bˆ¦
maxMemberCount (
exp (
maxShopSlotCount ("0
AttrToScore
type (Bˆ¦
ratio ("|

TalentMisc1
interval (2.TalentMisc.TalentScoreInterval
cultivation_coe ("
TalentScoreInterval
val ("‘
TournamentConf
id (Bˆ¦

week_index (Bˆ¦
level_limit (

dungeon_id (
start_time_stamp	 (
end_time_stamp
 (
pre_start_time_stamp (0
reward (2 .TournamentConf.TournamentReward
reward_type (
	reward_id (
end_die_num (
win_drop_id (	
fail_drop_id (	@
TournamentReward
win_num (
reward (2.STResource"V
TalentIntervals
talentId (Bˆ¦
localizationText (	
	intervals ("=

SigninGift
days (Bˆ¦
reward (2.STResource"`
ActivitySigninGift
days (Bˆ¦
activity_id (Bˆ¦
reward (2.STResource"F
ContinuousLoginPrize
days (Bˆ¦
prize (2.STResource"[
PostcardCell
postcard_id (Bˆ¦
cell_id (Bˆ¦
cost (2.STResource"W
PostcardPrize
postcard_id (Bˆ¦
prize (2.STResource
role_id ("D
JigsawPuzzleCell
cell_id (Bˆ¦
cost (2.STResource"H
JigsawPuzzlePrize
	light_num (Bˆ¦
prize (2.STResource"V
DropDisplayDailySignin
type (

id (
count (
probability ("S
DropDisplayShop
shopId (
resourceType (

id (
isSuit ("Ê
DropDisplayGacha
gachaId (
resourceType (
probability (@
qualityProbability (2$.DropDisplayGacha.QualityProbability8
QualityProbability
color (
probability ("S
AdditionalIncomeState
id (Bˆ¦
resType (
addtionalChance (":
TowerGroupUnlockLv
groupId (Bˆ¦
level ("x

TowerFloor
floor (Bˆ¦

dungeon_id (
record (
wipeCost (2.STResource

towerScore ("I
TowerFloorReward

towerFloor (Bˆ¦
reward (2.STResource"f
RefreshPrice
type (
count (
cost (2.STResource
buy (2.STResource:¦"œ
UIWindowConfig
name (	Bˆ¦
dontDestory (
depth (

fullScreen (
autoPlayTween (
antiAliasing (
navigatable (
showNavigation (
showResource	 (
showMessenger
 (
showSort (
showAchievement (
	showShare (

showSource (
openSE (	
closeSE (	
music (	
resumePreviousMusic (

ruleWindow (	:¦"|
ActorTalent
id (Bˆ¦

talentIcon (
actorID (
spectial (
	ratioIcon (
	groupType ("4
ResBuyLimit
type (Bˆ¦
	max_times ("9
DeployCount
level (Bˆ¦
mission_count ("S
ResourceCompose
	targetRes (2.STResource 
materialRes (2.STResource"§
DeployMission
id (Bˆ¦
mission_type (
icon (	
time (
cost (2.STResource
reward (2.STResource
need_role_num (*
role_con (2.DeployMission.Condition
story_id	 (
guild_mission
 (?
triggered_event_pool (2!.DeployMission.TriggeredEventPool<
	Condition
type (
param (	

real_param (/
TriggeredEventPool
level (

id ("W
TaskActivePointReward!
target_active_point (Bˆ¦
reward (2.STResource"v
TaskExtraActivePointReward
active_point_type (Bˆ¦
active_point (Bˆ¦
reward (2.STResource"c
TalentResetCost
actor_id (Bˆ¦
cost (2.STResource
cost_sub (2.STResource">
Tutorial
id (Bˆ¦
sequence (
status ("´
TutorialStep

id (
step (
endFlag (
	talkCount (
uiPositionX (
uiPositionY (
force	 (
clickAreaWidth
 (
clickAreaHeight (
whenShowWindow (	
whenOpenWindow (	
whenCloseWindow (	
delay (
	focusPosX (
	focusPosY ("-
IdsAttr
ids (
attr (2.STAttr"¸
	MedalInfo
id (Bˆ¦
order (
quality (
source (
effectAttrs (2.STAttr"
effectRoleIdAttr (2.IdsAttr#
effectActorIdAttr (2.IdsAttr"
effectBookIdAttr (2.IdsAttr
hide	 (

expireTime
 (	
expireTimestamp (
	expireDay (
category ("£
ActorQualityInfo
actorId (Bˆ¦
quality (Bˆ¦
talentResetUpgradeRate (
talentResetUpgradeFloor (
effectAttrs (2.STAttr"
effectRoleIdAttr (2.IdsAttr#
effectActorIdAttr (2.IdsAttr"
effectBookIdAttr (2.IdsAttr
specialAttrId	 ("U
ItemSourceIndex
id (Bˆ¦
text (	

windowName (	
params (	"g
Novel
id (Bˆ¦
actors (
covers (
url (	
isNovel (
active ("9
NovelReward
score (
reward (2.STResource"N
CollectionsNovelReward
novelId (Bˆ¦
rewards (2.NovelReward"F
RelationPvpPrize
winCount (Bˆ¦
prize (2.STResource"5
AttrTypeFight
attrType (Bˆ¦
coef ("Î
Role
id (Bˆ¦
animRes (	
emoteActorId ("
donate_refund (2.STResource"
decompose_res (2.STResource
favourActor (
portraitOffsetX (
portraitOffsetY ("N
RoleScoreAttr
id (Bˆ¦
score (Bˆ¦
attrs (2.STAttr"5
PatchLoadingHeroDisplay
dropActorDisplayID (")
ActorTag
id (Bˆ¦
tag ("2
FavourLevel
level (Bˆ¦
favour ("8
RoleFavourGiftList
roleId (Bˆ¦
item ("'
PatchConfig
CustomServiceUrl (	"µ
Mall
id (Bˆ¦
	productId (	
price (
isMonth (
limitLv (
reward (2.STResource
picBg (	
	isNewGift (
giftPage	 (
maxBuyCount
 (
maxCountClearInterval (
activity_id (
resourceType (
isDelay (
sustained_days ("Š
DelayReward
activity_id (Bˆ¦

mail_title (	
mail_content (	
mail_sender (	!
delay_reward (2.STResource"m
ActorTagInfo
id (Bˆ¦
color (

filterType (
displayOrder (

displayTag ("
ActorStarRandom
actorId (Bˆ¦
way (Bˆ¦5
starRandoms (2 .ActorStarRandom.ActorStarRandomC
talentCountRandoms (2'.ActorStarRandom.ActorTalentCountRandom-
ActorStarRandom
star (
rate (5
ActorTalentCountRandom
count (
rate ("Í
ActorPortrait
actorId (Bˆ¦
center (

bg (	-
position (2.ActorPortrait.PositionInfo
onlyAnimRes (Bˆ¦?
PositionInfo
scale (
Xoffset (
Yoffset ("Q
ActorSkillMaxLevel
actorId (Bˆ¦
star (Bˆ¦
maxLevel ("
GoldDungeonLevel
level (Bˆ¦

dungeon_id (
reward_gold (
reward_gold_kill (
roman_level ("m
DailyRefresh
id (Bˆ¦
type (Bˆ¦ 
refresh_res (2.STResource
refresh_limit ("z
MonologueConfig
id (Bˆ¦
desc (	
roles (
favour_level (
	monologue (	
cartoon ("
BoardSpeech
roleid (Bˆ¦
speechId (Bˆ¦
emoteAction (	

emoteVoice (	
	emoteText (	
vibrateStrength ("g
BoardSpeechCondition
id (Bˆ¦
type (
priority (
params (	
limit ("z

RacePVPSeg
id (Bˆ¦
seg (
sub_seg (
score (
protect (
rewards (2.STResource"C
RacePVPSegReward
seg (Bˆ¦
rewards (2.STResource"T
RacePVPBattleScoreReward
battle_score (Bˆ¦
rewards (2.STResource"c
RacePVPOpenTime
start_weekday (

start_hour (
end_weekday (
end_hour ("5
RacePVPRankScore
rank (Bˆ¦
score ("¨
ActivityExtraRewardRule
rule_id (Bˆ¦
type (
dungeon_start (
dungeon_end (
drop_condition_type (
drop_condition_params (!
drop_display (2.STResource
drop_id (	
specialIconOpen	 (	
specialIconClose
 (	
specialIconEffect (	"\
UnforcedTutorial
id (Bˆ¦

windowName (	
delay (
noCondition ("a
MsdkMessage
id (Bˆ¦
roleId (Bˆ¦
favourLevel (Bˆ¦
content (	"’
BoardVoice2Text
	voiceName (	Bˆ¦)
data (2.BoardVoice2Text.Voice2Text;

Voice2Text
localizationText (	
delayInvoke ("¶
MsdkUrl
QQTribe (	
QQGiftCenter (	
QQVipH5 (	
WXGameCenter (	
WXGiftCenter (	
XinYueH5 (	
PvpUrl (	
GiftExchange (	
LoginCS	 (	
	AccountCS
 (	

PurchaseCS (	

QA (	
UserProtocol (	
AnimeUrl (	
YingYongBaoUrl (	
UserProtocolEnUrl (	
PrivacyPolicyUrl (	
WsqUrl (	
TXRechargeUrl (	
KingCardUrl (	
EightEightUrl (	
OfficialAccountUrl (	
AnimeMovieUrl (	
CarnivalMonthUrl (	
CollectionActivityhUrl (	
AnimeMovieDeeplink (	
AnimeMovieDeeplinkIPad (	
AnimeMovieOctUrl (	
AnimeMovieOctDeeplink (	!
AnimeMovieOctDeeplinkIPad (	"
Sort
id (Bˆ¦
localization (	
	algorithm (
keys (2.Sort.SortKey
type ()
SortKey
keyName (	
param (:¦"<
CompatNotch
model (	Bˆ¦

notchWidth (:¦"•
LoadingConfig

id (
account_level (
pve_stage_number (
BigBg (	
UpBg (	
BattleLoading (	
close_level ("P
TeamDungeonGroup
groupId (Bˆ¦

dungeonIds (
	bgPicture (	"E
ShareReward
activity_id (Bˆ¦
reward (2.STResource"…
	Signboard
signboard_id (Bˆ¦
role_id (
type (
BoardOfEmote (
BoardOfAnimRes (
FavourLevel (

localScale (
unlock_type (
unlock_params	 (
active
 (

activeTime (	
roleNotepadRes (	"‚
FavourIncreaseRule
	rule_type (Bˆ¦
daily_increase_limit (
total_increase_limit (
increase_params ("…
RoleClassicAna
id (Bˆ¦
role (
FavourLevel (
	book_name (
chapter_number (
page_number ("ù
	ActorSkin
id (Bˆ¦
actorIds (
quality (
paletteIndexs (%
exchange_default (2.STResource#
exchange_gacha (2.STResource
sources (
displayIndex (
openTime	 (	
ignoreActorOpenTime
 ("ä
TaskInfo
id (Bˆ¦
priority (
	goal_type (
max_progress (

task_param (
reward (2.STResource
active_point
 (
pre_task_id (
goto_id (
category (
add_follow_task (
record_before_got (
refresh_after_got (
delete_after_reward (
sync_by_server (
life (
series (
check_when_refresh (
outstanding (
is_activity (
extra_active_point_type (
extra_active_point (%
actor_cultivation_activity_id ()
!actor_cultivation_reward_progress (
daily_refresh ("ª
RoleNoteVoiceConfig
id (Bˆ¦
role_id (

voice_type (

audio_name (	
	audio_src (
favour_level (
text (	
	role_name (	"I

ShareActor
actorId (Bˆ¦
	actorDesc (	
	wxSceneID ("v
EventReward
activity_id (Bˆ¦
activity_Type (Bˆ¦
star (Bˆ¦
reward (2.STResource"„
EventDungeon
activity_id (Bˆ¦
activity_Type (Bˆ¦

prefabPath (	
	dungeonId (
openCartoonId ("›
EventDungeonStage

dungeon_id (Bˆ¦

start_time (	
end_time (	
finish_cartoon_id (
activity_Type (
chapter_Name (	"¸
ActorDecompose
id (Bˆ¦
quality (Bˆ¦3
decompose_res (2.ActorDecompose.DecomposeResH
DecomposeRes
type (

id (
count (
isChips ("Ô
TriggeredEvent
pool_id (Bˆ¦
	is_active (
	ticket_id (
level (1
event (2".TriggeredEvent.TriggeredEventInfoC
TriggeredEventInfo

id (
type (
probability ("R
DiffActorInherit
actor_id (
activity_id (Bˆ¦
	target_id ("
MazeInfo
id (Bˆ¦

difficulty (
unlock_level (
max_actor_count (
	max_floor (
cost (2.STResource"A
MazePreCondition
id (Bˆ¦
type (
param ("ó
MazeBuff
buff_id (Bˆ¦
type (
duration (
persistent_type (
range ("
param (2.MazeBuff.BuffParam
	buff_icon (	
buff_big_icon (	:
	BuffParam
type (
value (
is_ratio ("‘
	MazePrize
prize_id (Bˆ¦,
treasure_box (2.MazePrize.TreasureBox>
TreasureBox
drop_id (	
quality (
count ("?
TempEntranceSwitchWindow
type (Bˆ¦
windows (	":
GachaFriendshipRate
rate (Bˆ¦
drop_id (	":
GachaScoreBg
activity_id (Bˆ¦
bg_name (	"P
WeekActivePointReward
active_point (Bˆ¦
reward (2.STResource"Ä
CollectionsScenes
id (Bˆ¦

textureRes (	
bgmRes (	
	isDayType (

unlockType (
openTime (	
	effectRes (	
taskId (
hide	 (
bgmName
 (	"w
NovelCredits
id (Bˆ¦)
actor (2.NovelCredits.actorInNovel*
actorInNovel
roleId (

cv (	"g
GameCredits
id (Bˆ¦ 
cName (	
eName (	
cDepartment (	
eDepartment (	"*
MazeTips
id (Bˆ¦
type ("¯
	ClawCrane
id (Bˆ¦
res (2.STResource
stock (
probability (
is_count_limit (
is_peripheral (
	is_unique (
get_anim ("D
ClawCraneStock
id (Bˆ¦
day (Bˆ¦
stock ("T
ClawCraneForceCount
id (Bˆ¦
day (Bˆ¦
force_drop_count ("¤
CultivationDegree
param_hp (
	param_atk (
param_def_physical (
param_def_magic (
param_crit_rate (
param_crit_dmg (
param_skill_one (
param_skill_two (
param_skill_three	 (
param_talent_exclusive
 (
param_skill_sub ("¢
ActivityItemIconInfo

activityId (Bˆ¦
activityType (1
	iconInfos (2.ActivityItemIconInfo.iconInfo'
iconInfo

id (
resIcon (	"×
WelfareConfig
name (	Bˆ¦
tabIconAtlasPath (	
tabIconSpriteName (	

prefabName (	
	tabLocale (	*
relatedActivityType (2.ActivityType
relatedActivityIds (
params (	"r
ChallengeModeDungeon
activity_id (Bˆ¦

dungeon_id (
dungeon_reward (
dungeon_pic (	"^
ChallengeModeRankReward
type (Bˆ¦
rank (Bˆ¦
reward (2.STResource"f
ChallengeModeScoreReward
score (Bˆ¦

dungeon_id (Bˆ¦
reward (2.STResource"_
ChipDungeonToCultivationDungeon
chipDungeonId (Bˆ¦
cultivationPVPDungeonId ("€
GuildDungeon
	dungeonId (Bˆ¦

activityId (
assistBuffId (
otherAssistBuffId (3
challengeBuffs (2.GuildDungeon.challengeBuff
scoreUpLimit (
exchangeScaleForGuild (
exchangeScaleForPerson (
dungeonType	 (

scoreDelta
 (
openDay (
scoreUpLimitForGuild (0
challengeBuff

id (
targetScore ("R
GuildDungeonAssistScoreReward
score (Bˆ¦
rewards (2.STResource"o
 GuildDungeonChallengeScoreReward

dungeon_id (Bˆ¦
score (Bˆ¦
rewards (2.STResource"u
#GuildDungeonChallengeProgressReward

dungeon_id (Bˆ¦
progress (Bˆ¦
rewards (2.STResource"´
SemiFixedDungeon
	dungeonId (Bˆ¦
npcId (/
addtions (2.SemiFixedDungeon.npcAdditionG
npcAddition
value (
isRatio (
res (2.STResource"®

DungeonNPC
npc_id (Bˆ¦
actor_id (
quality (
star (
level (
attrs (2.STAttr
talents (2.TalentInfo
skills_level ("õ
	FightBuff
buff_id (Bˆ¦
group_id (
	last_time (

last_round (
effect_dungeon_group (
end_time (	#
team1 (2.FightBuff.AffixList#
team2 (2.FightBuff.AffixList
	AffixList
	affixList ("¶
ActorCultivation
activity_id (Bˆ¦
actor_id (
actor_quality_min (
max_progress (
daily_task_max_progress (#
res_active_vip (2.STResource"¢
ActivityBuffGoods
goodId (Bˆ¦
grade (Bˆ¦#
buffType (2.ActivityBuffType
buffId (
cost (2.STResource

spriteName (	"A
ActivityBuffShop
activityType (Bˆ¦
	buffGoods ("‘
ActorCultivationProgressReward
activity_id (Bˆ¦
progress (Bˆ¦
reward (2.STResource

reward_vip (2.STResource"G
PurblePairsReward
matched_pairs (
reward (2.STResource"Ë
ProfitDetail
id (Bˆ¦
type (
add (
is_ratio (
time (
dungeon_type (
res_type (
	func_type (
addtion	 (2.STResource
dungeonGroups ("_
ActorCultivationEmote
actorId (Bˆ¦
scale (
Xoffset (
Yoffset ("R
ActorUnlockEffect
actorId (Bˆ¦
	actorPath (	
actorBgPath (	":
CostumeInfo
maxCount (Bˆ¦
unlockLevel ("À
ActivityShopGoods
shop_good_id (Bˆ¦
activity_id (
activity_type (
costs (2.STResource
goods (2.STResource
	buy_limit (
res (2.STResource"Ñ
ActivityInviteActorInfo
activityType (Bˆ¦
roleId (Bˆ¦
actorId (
index (
inviteCount (
goodId (
animRes (	
animResOffsetX (
animResOffsetY	 (bproto3
æY
xlsx_server.protocommon.protooptions.proto"L
LocalizationServer
id (	Bˆ¦

eu (	
chs (	
cht (	"À
DropInfo
drop_id (	$
	drop_item (2.DropInfo.DropObj
	is_weight (D
DropInfoDynamic
drop_id (	 
drops (2.DropInfo.DropObj£
DropObj
probability (
retrieve_id (
res (2.STResource
is_guarantee (
is_drop_purple_exclude (
is_drop_orange_exclude ("c
PlayerInitConfig

initActors ("
initResources (2.STResource
initSignboardId ("P
ActorInitStar
actorId (Bˆ¦
initSkillIds (

gachaScore ("v
Retrieve
id (Bˆ¦
res_type (
field (	
low (
high (
results (2.STResource"£
ShopType
shopId (Bˆ¦
player_level (
refreshDayOfMonth (
refreshDayOfWeek (
refreshTimeInDay (	
refreshLimitNotify (
refreshLimit (
refreshFree	 (
maxPosition
 (
shufflePosition (!
display_cost (2.STResource
refresh_old (
show (
activity (
guild (
vipSuperExtraCount (
client_show (
version ("Y
ShopManualRefresh
shopId (Bˆ¦
times (Bˆ¦
cost (2.STResource"e
ShopSale
shopId (Bˆ¦
levelLow (Bˆ¦
	levelHigh (Bˆ¦

shopGoodId ("À
	ShopGoods

shopGoodId (
cost (2.STResource
good (2.STResource
chance (
buyLimit (
can_refresh (
guildLevelLimit (
goods (2
.ShopGoods"Ô
TaskDailyRefresh

refresh_id (Bˆ¦
level (Bˆ¦
	level_max (
role_id (
favour (,
	refreshes (2.TaskDailyRefresh.Refresh/
Refresh
task_id (
probability ("Ò
TalentTypeRandom
actorId (Bˆ¦
talentIndex (Bˆ¦.
	talentIds (2.TalentTypeRandom.talent_id3
resetTalentIds (2.TalentTypeRandom.talent_id'
	talent_id

id (
weight ("Ç
TalentValueRandom
talentId (Bˆ¦
talentEffectValue (Bˆ¦
probability (
sameIdRangeMin (
sameIdRangeMax (
diffentIdRangeMin (
diffentIdRangeMax ("Õ

Activities
type (Bˆ¦
activity_id (Bˆ¦
time_preheat (	

time_start (	
time_end (	
timestamp_preheat (
timestamp_start (
timestamp_end	 (
weekdays
 (
forever (
category (

time_close (	
timestamp_close (
drop_floor_channel (
level_limit (.
day_open_time (2.Activities.DayOpenTime
season (
weekday_preheart (
	time_type (
version_limit (	
pre_open_time (c
DayOpenTime

time_start (	
time_end (	
timestamp_start (
timestamp_end (":
ActivityTask
activity_id (Bˆ¦
task_id ("á
AsyncPvpMatchRule
levelLow (Bˆ¦
	levelHigh (Bˆ¦4
target_rules (2.AsyncPvpMatchRule.target_rulee
target_rule
levelOffsetLow (
levelOffsetHigh (
	winCntLow (

winCntHigh ("³
AsyncPvpRobot
uid (	
level (
name (	
winCnt (
levelLow (
	levelHigh (
starLow (
starHigh (
	headphoto	 (,
scale
 (2.AsyncPvpRobot.RobotAttrScale_
RobotAttrScale
attack (

hp (
physical_defense (
magical_defense ("d
	DropFloor
channel (Bˆ¦
count (Bˆ¦
score (
drop_id (	
way ("F
ActorTalentDropFloorScore
actorQuality (Bˆ¦
score (";
localization
tag (	Bˆ¦
chs (	
enu (	"B
DeployMissionLevelNum
level (Bˆ¦
mission_type ("‘
CommonSettingsConfC
PUSH_MODE_NAMOMSG (2(.CommonSettingsConf.PUSHMODENAMOMSGEntry6
PUSHMODENAMOMSGEntry
key (	
value (:8"±
FciSettingsConf
DUNGEON_KEEP (
DUNGEON_KEEP_CHEAT (
TOURNAMENT_KEEP_TIME (
SHOP_MAX_TRADE_RECORD (
GACHA_MAX_TRADE_RECORD (
PUSH_MSG_ONE_TIME (
PLAYER_OFFLINE_TIME_MS
 (
DB_LOG (
PURCHASE_TEST_MONEY ('
PURCHASE_TRADE_CODE_EXPIRE_TIME (
IAP_URL (	
IAP_SANDBOX_URL (	
IAP_PUBLIC_KEY (	
IAP_VERIFY_TIMEOUT (
WXPAY_PREPAY_HEADER (	
WXPAY_APP_ID (	
WXPAY_MCH_ID (	
	WXPAY_KEY (	
WXPAY_SANDBOX (
WXPAY_PREPAY_URL (	
WXPAY_NOTIFY_URL (	
WXPAY_ORDER_QUERY_URL (	
ALIPAY_METHOD (	
ALIPAY_PRODUCT_CODE (	
ALIPAY_PURCHASE_TIMEOUT (	
ALIPAY_SANDBOX (
ALIPAY_APP_ID  (	
ALIPAY_SELLER_ID! (	
ALIPAY_NOTIFY_URL$ (	
DEFAULT_LANGUAGE( (	
MSDK_Wechat) (	
MSDK_QQ* (	

MSDK_Guest+ (	
MIDAS_PAY_OPEN- (
TOKEN_VERIFY_CACHE_SIZE. (!
TOKEN_VERIFY_CACHE_EXPIRE/ (
SIGN_LENGTH_MAX2 ( 
MAX_PLAYER_MESSAGE_BOARD4 (
DB_UPDATE_CHECK_MS5 (
REFRESH_SHOP_FORCE7 (
CHECK_TOKEN_EXPIRE8 (7
TELECOM_NAME9 (2!.FciSettingsConf.TELECOMNAMEEntry 
RECOMMEND_STRANGER_COUNT< (
RECOMMEND_FRIEND_COUNTQ (
MAX_MESSAGE_SAVE_NUM= (
BURSTLINK_ROBOT_SWITCH> (!
DAMMU_MIN_REQUEST_DIFF_MS? (
DANMU_CACHE_SIZE@ (
DANMU_MAX_PER_FRAMEA (
DANMU_EXPIREB (
DANMU_MIN_COUNTC (
FIGHT_CHECK_STRATEGYD (
FIGHT_PRE_CHECK_PERCENTE (
FIGHTSVR_LOAD_LIMITF (#
TEAMDUNGEON_ROOM_MAX_PLAYERG (
NEW_PLAYER_NAME_PREFIXH (	"
SEC_CHEAT_CHECK_DAMAGE_MAXI ($
DUNGEON_OVER_DAMAGE_BAN_TIMEJ (
GACHA_ACTOR_VIDEO_URLK (	
MAX_MONTH_DAY_BUYL (
DEFAULT_SOCIAL_PICTUREN (	'
PRIVATE_CHAT_TO_STRANGERS_COUNTO (2
TELECOMNAMEEntry
key (	
value (	:8"ñ
Account91SettingsConf
ENTRY_TYPE_SUPPORTED (	
ENTRY_TYPE_NOT_VERIFY (	
APP_KEY_TYPES (	
APP_KEY_TYPES_SAFE (	
SOCIAL_TYPE_SUPPORTED (	
ENTRY_TYPE_REALNAME (	
USER_ID_ALLOWD_APPS (	
NEW_ACCOUNT_GM (
MANAGE_EXPIRE_TIME	 (
BBRR_APPIDS
 (	&
ACCOUNT_SESSION_EXPIRE_SECONDS (#
SOCIAL_TOKEN_SUCCESS_STRING (	
MANAGER_TOKEN_EXPIRE (
REAL_SEND_VERIFY_CODE (
MSDK_TOKEN_EXPIRE_TIME (
MAX_DAY_REGISTER_NUM ("!
DirSettingsConf
FCIURL (	"H
PushSettingsConf

MAX_PLAYER ( 
MAX_CHAT_CHANNEL_AVERAGE ("I

GuildSkill
id (Bˆ¦
max_use_count (

score_cost ("3
GachaScoreShop
id (Bˆ¦
good_id ("b
GachaScoreShopGoods
good_id (Bˆ¦
cost (2.STResource
good (2.STResource"K
GachaNormalOrangeChance
not_get_count (Bˆ¦
probability ("L
GachaLimitedOrangeChance
not_get_count (Bˆ¦
probability ("|
GachaAddDrop
pool_id (Bˆ¦(
drop_res_info (2.DropInfo.DropObj

start_time (	
start_timestamp ("<
SealCell
cell_id (Bˆ¦
cost (2.STResource"œ

SealReward
	reward_id (Bˆ¦
reward_kind (
reward_index (
reward (2.STResource
display (2.STResource
quality ("e
BurstLinkPVPReward
id (Bˆ¦
reward (2.STResource 
next_reward (2.STResource"P
CongratsMailConfig
role_id (Bˆ¦
time (	Bˆ¦
mail_id ("Z
FavourLvCongratsMail
role_id (Bˆ¦
favour_level (Bˆ¦
mail_id ("v
MailTemplate
mail_id (Bˆ¦
sender (	
title (	
content (	

attachment (2.STResource"9
BurstLinkRobotGroup
id (Bˆ¦
actor_id ("~
RoleFavourPresent
role_id (Bˆ¦
favour_level (
random_present_drop_id (	
present (2.STResource"O
VoteRoleTargetReward
target_count (Bˆ¦
reward (2.STResource"2
VoteRoleInfo
role_id (Bˆ¦
url (	"”
VoteRoleConfig
	vote_cost (2.STResource 
vote_reward (2.STResource
	vote_bang (2.STResource 
vote_cost_exchange_count ("r
NoticeReward
id (Bˆ¦
name (	
rewards (2.STResource
images (	
rewards_name (	"g
DynamicSwitch
api_retry_init_interval (
api_retry_add_interval (
api_retry_cnt ("¿
VoteRandValue

id (
voteStartTime (	
voteEndTime (	
voteStartTimeStamp (
voteEndTimeStamp (
voteMinRandValue (
voteMaxRandValue (
rate ("g
DungeonCheckCheat
id (Bˆ¦
para (
dps (
check_round (
check_hp ("±

GiftConfig
	gift_type (Bˆ¦
gift_platform (
is_command_gift (
type_max_use_count (
code_max_use_count (
effect_begin_time_str (	
effect_end_time_str (	
effect_begin_time (
effect_end_time (
res_list (2.STResource

mail_title (	
mail_sender	 (	
mail_content
 (	
command_str (	
npc_role_id (
talk_content (	
effect_count ("<
ReportBanConfig
level (Bˆ¦
report_score ("D
ChatBanConfig
chat_ban_times (Bˆ¦
chat_ban_time ("Q
WebSigninTargetReward
target_count (Bˆ¦
rewards (2.STResource"G
WebSigninDailyReward
day (Bˆ¦
rewards (2.STResource"‹
WebVoteRole
role_id (Bˆ¦
image (	
type (
sort_id (
	role_name (	

novel_name (	
novel_id ("·
SubscribeConfig
msg_id (Bˆ¦

cycle_time (
platform (
start_time_stamp (
end_time_stamp (

start_time (	
end_time (	
is_month ("v
MazeGenerate

difficulty (Bˆ¦
floor (Bˆ¦
generate_map_id (
generate_event_line_id ("‘
MazeMapGenerate
generate_map_id (Bˆ¦
prize_id ( 
branch_count_lower_limit ( 
branch_count_upper_limit (!
branch_length_upper_limit (3
type_weight (2.MazeMapGenerate.MapTypeWeight=
buff_probability (2#.MazeMapGenerate.MapBuffProbability6
MapTypeWeight
map_dimension (
weight (:
MapBuffProbability
buff_id (
probability ("c
MazeEventLineGenerate$
generate_event_line_id (Bˆ¦
	line_type (
	event_ids ("„
MazeEventGenerate
event_id (Bˆ¦

event_type (Bˆ¦
event_weight (
event_param (	H
pre_cond_probability (2*.MazeEventGenerate.PreConditionProbabilityH
PreConditionProbability
pre_condition_id (
probability ("£
FightTimeOutConf
dungeon_type (Bˆ¦
	init_game (
	load_game (
	round_end (
request_card (
use_card (

fight_over ("8
MailExpireTime
type (Bˆ¦

expire_day ("^
ConditionReward#
condition_reward_type (Bˆ¦&
manual_box_reward (2.STResource"L
DanmuSwitch
res_id (	Bˆ¦

switch_get (
switch_send (">
RacePVPDungeon
activity_id (Bˆ¦
	dungeonId ("è
BurstlinkActorInfo
id (Bˆ¦
quality (
openTime
 (	
openTimeStamp (
star (
level (
kind (
version	 (
attrs (2.STAttr
talents (2.TalentInfo
skills_level ("@
BurstlinkDungeon
activity_id (Bˆ¦
	dungeonId ("F
GuildChallengeDungeon
activity_id (Bˆ¦

dungeon_id ("Ş
 ActorCultivationTaskRefreshDaily
activity_id (
vip_only (<
	refreshes (2).ActorCultivationTaskRefreshDaily.RefreshU
Refresh
task_id (
probability (
	level_min (
	level_max ("G
ActorCultivationTaskRefreshOnce
activity_id (
task_id (bproto3