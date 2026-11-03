
 dungeon-recovered-original.protooptions.protocommon.protogamedb.proto
xlsx.protofight.prototriggered_event.protodungeon.proto"&
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

res_change (2.STResourceDynamic:~ÒóApiRacePVPDungeonViewÊó5/racepvp-dungeon/,/racepvp-dungeon/<int:dungeon_uid>/Úó(æ®‹æ¢¦ä¹‹å¢ƒå‰¯æœ¬è¿›å…¥ï¼Œé€€å‡º,æŸ¥è¯¢"ú
ApiRacePVPDungeonBalancek
result
cur_battle_score (
high_battle_score (,
balance_info (2.DbDungeon.BalanceInfo:qÒóApiRacePVPDungeonBalanceViewÊó+/racepvp-dungeon/<int:dungeon_uid>/balance/Úóæ®‹æ¢¦ä¹‹å¢ƒå‰¯æœ¬ç»“ç®—ä¿¡æ¯"j
ApiUnlockDungeon
star (

dungeon_id (:4ÒóDungeonUnlockViewÊó/unlockdun/Úóè§£é”å‰¯æœ¬"¬
ApiDungeonBalanceÁ
result,
balance_info (2.DbDungeon.BalanceInfo-
chapter_summary_info (2.ChapterSummary)
chapter_info (2.ApiChapterInfo.get/

event_info (2.DBTriggeredEvent.EventInfo:SÒóDungeonBalanceViewÊó#/dungeon/<int:dungeon_uid>/balance/Úóå‰¯æœ¬ç»“ç®—ä¿¡æ¯"Ä
ApiDungeonActive!
request (2.FightActiveData
result:‚ÒóDungeonActiveViewÊó"/dungeon/<int:dungeon_uid>/active/ÚóCå‰¯æœ¬è¿›è¡Œæƒ…å†µ,åŒ…æ‹¬ç›®æ ‡å®Œæˆæƒ…å†µ,åªèƒ½patch active info"×
ApiTowerDungeonActive!
request (2.FightActiveData
result:ÒóApiTowerDungeonActiveViewÊó(/tower-dungeon/<int:dungeon_uid>/active/ÚóCåˆ·å¡”å‰¯æœ¬è¿›è¡Œæƒ…å†µ,åŒ…æ‹¬ç›®æ ‡å®Œæˆæƒ…å†µ,patch active info"‚
ApiSelectedActorGroup
select_actor_group_id ('
result
select_actor_group_id (: ÒóApiSelectedActorGroupViewÊó/SelectedGroupId/<string:mode>/Úó\mode=StoryMode|ChallengeMode|tower|activity-dun|racePVP|gold-dungeon|asyncPvp|triggeredEvent"û
ApiBattleRoom
actor_group_uid (
	room_name (	

dungeon_id ('
result
result (2.DbBattleRoom:€ÒóBattleRoomViewÊó(/battleroom/,/battleroom/<int:room_uid>/Úó>åŸºæœ¬å¯¹æˆ˜æˆ¿é—´æ“ä½œ,poståˆ›å»ºæˆ¿é—´,ä¸å¸¦idè·å¾—åˆ—è¡¨"Ô
ApiBattleRoomMembers%
request (2.DbBattleRoom.Member.
result$
result (2.DbBattleRoom.Member:eÒóBattleRoomMemberViewÊó#/battleroom/<int:room_uid>/members/Úó"putåŠ å…¥æˆ¿é—´,deleteé€€å‡ºæˆ¿é—´"£
ApiBattleInstance+
result!
result (2.DbBattleInstance:aÒóBattleInstanceViewÊó0/battleinstance/,/battleinstance/<int:room_uid>/Úópostæˆ¿é—´å¼€æ¸¸æˆ"}
ApiBattleInstanceBalance
result:WÒóBattleInstanceBalanceViewÊó'/battleinstance/<int:room_uid>/balance/Úópost ç»“ç®—"
ApiBattleMatch
post"§
ApiActivityDungeonSummary'
result
select_actor_group_id (:aÒóApiActivityDungeonSummaryViewÊó/activity-dungeon/summary/Úóè·å–æ´»åŠ¨å‰¯æœ¬æ€»ä½“æ‘˜è¦"ï
ApiActivityDungeonRecordL
resultB
result (22.DBGamePlayer.ActivityDungeonRecords.DungeonRecord:„ÒóApiActivityDungeonRecordViewÊóD/activity-dungeon/record/,/activity-dungeon/record/<int:dungeon_id>/Úóè·å–æ´»åŠ¨å‰¯æœ¬è®°å½•"ß
ApiEventDungeonRecordI
result?
result (2/.DBGamePlayer.EventDungeonRecords.DungeonRecord:{ÒóApiEventDungeonRecordViewÊó>/event-dungeon/record/,/event-dungeon/record/<int:dungeon_id>/Úóè·å–å‘¨å›å‰¯æœ¬è®°å½•"¶
ApiActivityDungeonBalance0
result&
result (2.DbDungeon.BalanceInfo:gÒóApiActivityDungeonBalanceViewÊó,/activity-dungeon/<int:dungeon_uid>/balance/Úóå‰¯æœ¬ç»“ç®—ä¿¡æ¯"³
ApiTowerDungeonBalance0
result&
result (2.DbDungeon.BalanceInfo:gÒóApiTowerDungeonBalanceViewÊó)/tower-dungeon/<int:dungeon_uid>/balance/Úóåˆ·å¡”å‰¯æœ¬ç»“ç®—ä¿¡æ¯"í
ApiCheckCheat1

actor_info (2.ApiCheckCheat.FightActorInfo=
FightActorInfo
	player_id (	
actors (2.STActor
result
result (:PÒóDungeonCheckCheatViewÊó#/checkcheatstart/<int:dungeon_uid>/Úóæ£€æµ‹ä½œå¼Š"o
ApiWeekActivityFull
result
result (:>ÒóWeekActivityFullViewÊó/weekactivityfull/Úóå¼‚å®å…¨å¼€"å
ApiTeamDungeonGroupIdss
result:
result (2*.ApiTeamDungeonGroupIds.result.ResultEntry-
ResultEntry
key (
value (:8:VÒóTeamDungeonGroupsÊó/teamdungeongroups/Úó&å¾—åˆ°è¿™å‘¨ç»„é˜Ÿå‰¯æœ¬çš„å‰¯æœ¬ç»„ID"r
ApiTeamDungeonSvrUrl
result
url (	:CÒóTeamDungeonUrlViewÊó/teamdungeonurl/Úóè·å–ç»„é˜Ÿå‰¯æœ¬url"­
ApiTeamDungeonBalance¶
result,
balance_info (2.DbDungeon.BalanceInfoH
player_damages (20.ApiTeamDungeonBalance.result.PlayerDamagesEntry4
PlayerDamagesEntry
key (	
value (:8:[ÒóTeamDungeonBalanceViewÊó'/teamdungeon/<int:dungeon_uid>/balance/Úóç»„é˜Ÿå‰¯æœ¬ç»“ç®—"Ô
ApiEventDungeonActive!
request (2.FightActiveData
result:ÒóEventDungeonActiveViewÊó(/event-dungeon/<int:dungeon_uid>/active/ÚóCå‰¯æœ¬è¿›è¡Œæƒ…å†µ,åŒ…æ‹¬ç›®æ ‡å®Œæˆæƒ…å†µ,åªèƒ½patch active info"˜
ApiTriggeredDungeon
select_actor_group_id (
	event_key (	
activity_type (
activity_id (
aura_player_id (	
aura_actor_id (
aura_stranger (
aura_actor_quality (E
result
dungeon_uid (&
	base_info (2.DbDungeon.BaseInfo:zÒóApiTriggeredDungeonViewÊó9/triggered-dungeon/,/triggered-dungeon/<int:dungeon_uid>/Úóè§¦å‘å¼å‰¯æœ¬è¿›å…¥ï¼Œé€€å‡º"Â
ApiTriggeredDungeonBalance0
result&
result (2.DbDungeon.BalanceInfo:rÒóApiTriggeredDungeonBalanceViewÊó-/triggered-dungeon/<int:dungeon_uid>/balance/Úóè§¦å‘å¼å‰¯æœ¬ç»“ç®—ä¿¡æ¯"ã
ApiTriggeredDungeonActive!
request (2.FightActiveData
result:˜ÒóApiTriggeredDungeonActiveViewÊó,/triggered-dungeon/<int:dungeon_uid>/active/ÚóCå‰¯æœ¬è¿›è¡Œæƒ…å†µ,åŒ…æ‹¬ç›®æ ‡å®Œæˆæƒ…å†µ,åªèƒ½patch active info"
ApiMazeDungeon

cell_index (ù
result
dungeon_uid (&
	base_info (2.DbDungeon.BaseInfo)
enemy_record (2.DbMaze.EnemyRecord
energySp (
energyMp (5
	my_record (2".ApiMazeDungeon.result.ActorRecord,
ActorRecord
	actor_uid (

hp (:lÒóApiMazeDungeonViewÊó//maze-dungeon/,/maze-dungeon/<int:dungeon_uid>/Úóè¿·å®«æˆ˜æ–—å‰¯æœ¬è¿›å…¥,é€€å‡º"î
ApiMazeDungeonBalancen
result&
result (2.DbDungeon.BalanceInfo
is_maze_over (&
	auto_move (2.ApiMazeMove.result:eÒóApiMazeDungeonBalanceViewÊó(/maze-dungeon/<int:dungeon_uid>/balance/Úóè¿·å®«æˆ˜æ–—å‰¯æœ¬ç»“ç®—"ç
ApiMazeDungeonActive!
request (2.FightActiveData
result:¡ÒóApiMazeDungeonActiveViewÊó'/maze-dungeon/<int:dungeon_uid>/active/ÚóVè¿·å®«æˆ˜æ–—å‰¯æœ¬è¿›è¡Œæƒ…å†µ,è¡€é‡ç»§æ‰¿,åŒ…æ‹¬ç›®æ ‡å®Œæˆæƒ…å†µ,patch active info"…
DBChallengeModeRank
	player_id (	
player_name (	
score (9
actor_group (2$.DBChallengeModeRank.ActorGroupIndex

dungeon_id (
activity_id (
	is_reward	 (
KeyField
 (	.
ActorGroupIndex

id (
quality ("Ã
ApiChallengeModeRank.
get'
	rank_info (2.DBChallengeModeRank:{ÒóApiChallengeModeRankViewÊó</challenge-mode-rank/top/<int:topN>/dungeon/<int:dungeon_id>Úóå½“å‰æŒ‘æˆ˜æ¨¡å¼æ’è¡Œæ¦œ"X
DBChallengeModeRankSeason

dungeon_id ('
	rank_info (2.DBChallengeModeRank"ò
DBChallengeModeScoreReward
	player_id (	D
reward_record (2-.DBChallengeModeScoreReward.RewardRecordEntry
	RewardGot

reward_got (Z
RewardRecordEntry
key (	4
value (2%.DBChallengeModeScoreReward.RewardGot:8"Ú
ApiChallengeModeReward

dungeon_id (
score (Ô
postG
reward_records (2/.ApiChallengeModeReward.post.RewardRecordsEntry&

res_change (2.STResourceDynamic[
RewardRecordsEntry
key (	4
value (2%.DBChallengeModeScoreReward.RewardGot:8Æ
getF
reward_records (2..ApiChallengeModeReward.get.RewardRecordsEntryB
score_record (2,.ApiChallengeModeReward.get.ScoreRecordEntry"

RecordList
record_score ([
RewardRecordsEntry
key (	4
value (2%.DBChallengeModeScoreReward.RewardGot:82
ScoreRecordEntry
key (	
value (:8:}ÒóApiChallengeModeRewardViewÊó/challenge-mode/reward/Úó@æŒ‘æˆ˜æ¨¡å¼é¢†å–å¥–åŠ±;æŸ¥è¯¢æŒ‡å®šå‰¯æœ¬çš„å¥–åŠ±é¢†å–è®°å½•"¸
ApiChallengeModeSeason
score (.
get'
	rank_list (2.DBChallengeModeRank:_ÒóApiChallengeModeSeasonViewÊó'/challenge-mode/season/<int:dungeon_id>Úóèµ›å­£è®°å½•æŸ¥è¯¢"Û
ApiSemiFixedDungeon

dungeon_id (
activity_type	 (
activity_id
 (
aura_player_id (	
aura_actor_id (
aura_stranger (
aura_actor_quality (
multi_power (
	event_key (	m
result
dungeon_uid (&
	base_info (2.DbDungeon.BaseInfo&

res_change (2.STResourceDynamic:ŠÒóApiSemiFixedDungeonViewÊó9/semifixed-dungeon/,/semifixed-dungeon/<int:dungeon_uid>/Úó.post-åŠå›ºå®šé€‰äººå‰¯æœ¬è¿›å…¥,delete-é€€å‡º"Í
ApiSemiFixedDungeonBalance6
result,
balance_info (2.DbDungeon.BalanceInfo:wÒóApiSemiFixedDungeonBalanceViewÊó-/semifixed-dungeon/<int:dungeon_uid>/balance/Úó post-åŠå›ºå®šé€‰äººå‰¯æœ¬ç»“ç®—"Ù
ApiSemiFixedDungeonActive!
request (2.FightActiveData:˜ÒóApiSemiFixedDungeonActiveViewÊó,/semifixed-dungeon/<int:dungeon_uid>/active/ÚóCå‰¯æœ¬è¿›è¡Œæƒ…å†µ,åŒ…æ‹¬ç›®æ ‡å®Œæˆæƒ…å†µ,åªèƒ½patch active info"é
ApiSemiFixedDungeonActorGroupA

group_info (2-.DbSemiFixedDungeonInfo.SemiFixedDungeonGroupK
resultA

group_info (2-.DbSemiFixedDungeonInfo.SemiFixedDungeonGroup:·Òó!ApiSemiFixedDungeonActorGroupViewÊó//semifixed-dungeon/actorgroup/<int:dungeon_id>/Úó[åŠå›ºå®šé€‰äººå‰¯æœ¬å‡ºæˆ˜è§’è‰²ç»„(get-è·å¾—å‡ºæˆ˜è§’è‰²ç»„ï¼Œput-è®¾ç½®å‡ºæˆ˜è§’è‰²ç»„)" 
ApiActivityStarReward
star (ñ
getE
reward_summary (2-.ApiActivityStarReward.get.RewardSummaryEntry=
reward_summary_info

reward_got (

total_star (d
RewardSummaryEntry
key (=
value (2..ApiActivityStarReward.get.reward_summary_info:8.
post&

res_change (2.STResourceDynamic:ÔÒóApiActivityStarRewardViewÊóg/activity/star-reward/<int:activity_type>/<int:activity_id>/,/activity/star-reward/<int:activity_type>/ÚóHget-æŸ¥è¯¢æ´»åŠ¨å‰¯æœ¬å¥–åŠ±ä¿¡æ¯ï¼Œpost-é¢†å–æ´»åŠ¨å‰¯æœ¬æ˜Ÿçº§å¥–åŠ±"˜
ApiActivityRecordB
result8
records (2'.DbActivityDungeonRecords.DungeonRecord:¾ÒóApiActivityRecordViewÊó€/activity/record/<int:activity_type>/<int:activity_id>/,/activity/record/<int:activity_type>/<int:activity_id>/<int:dungeon_id>/Úóget-è·å–æ´»åŠ¨å‰¯æœ¬è®°å½•"‡
DbGuildDungeonRankDetail
	player_id (	
player_name (	
score (>
actor_group (2).DbGuildDungeonRankDetail.ActorGroupIndex

dungeon_id (
guild_id (
KeyField
 (	<
ActorGroupIndex

id (
quality (
star ("Ü
DbGuildDungeonRecord
guild_id (@
dungeon_record (2(.DbGuildDungeonRecord.DungeonRecordEntry”
DungeonRecord
dungeon_type (

dungeon_id (
total_damage (
is_finished (
enemyHpList (
notified_progress (Y
DungeonRecordEntry
key (2
value (2#.DbGuildDungeonRecord.DungeonRecord:8"¾
DbGuildDungeonDetail
guild_id (1
detail (2!.DbGuildDungeonDetail.DetailEntry“
DetailG
dungeon_detail (2/.DbGuildDungeonDetail.Detail.DungeonDetailEntryİ
DungeonDetail
	player_id (	
score (O
actor_group (2:.DbGuildDungeonDetail.Detail.DungeonDetail.ActorGroupIndex
time (K
ActorGroupIndex

id (
quality (
star (
level (`
DungeonDetailEntry
key (	9
value (2*.DbGuildDungeonDetail.Detail.DungeonDetail:8K
DetailEntry
key (+
value (2.DbGuildDungeonDetail.Detail:8"Ğ
DbAssistMemberRank<
dungeon_score (2%.DbAssistMemberRank.DungeonScoreEntry(
	ScoreInfo
score (
time (R
DungeonScoreEntry
key (,
value (2.DbAssistMemberRank.ScoreInfo:8"²
ApiGuildDungeonSummary`
DungeonRecord
dungeon_type (

dungeon_id (
is_finished (
progress (Æ
get>
dungeon_records (2%.ApiGuildDungeonSummary.DungeonRecord
challenge_status (8
member_rank (2#.ApiGuildDungeonSummary.get.ChaRank+
ChaRank
	player_id (	
score (:mÒóApiGuildDungeonSummaryViewÊó&/guild/<int:guild_id>/dungeon/summary/Úó!è·å–å…¬ä¼šæ´»åŠ¨çš„å‰¯æœ¬è®°å½•"
ApiGuildDungeon
select_actor_group_id (

dungeon_id ([
result
dungeon_uid (&
	base_info (2.DbDungeon.BaseInfo
enemy_hplist (:kÒóApiGuildDungeonViewÊó1/guild-dungeon/,/guild-dungeon/<int:dungeon_uid>/Úóå…¬ä¼šå‰¯æœ¬è¿›å…¥ï¼Œé€€å‡º"×
ApiGuildDungeonActive!
request (2.FightActiveData
result:ÒóApiGuildDungeonActiveViewÊó(/guild-dungeon/<int:dungeon_uid>/active/ÚóCå‰¯æœ¬è¿›è¡Œæƒ…å†µ,åŒ…æ‹¬ç›®æ ‡å®Œæˆæƒ…å†µ,åªèƒ½patch active info"Î
ApiGuildDungeonBalanceQ
result&
result (2.DbDungeon.BalanceInfo
score (
progress (:aÒóApiGuildDungeonBalanceViewÊó)/guild-dungeon/<int:dungeon_uid>/balance/Úóå‰¯æœ¬ç»“ç®—ä¿¡æ¯"ç
ApiGuildDungeonRecordØ
getE
dungeon_record (2-.ApiGuildDungeonRecord.get.DungeonRecordEntry
enemyHpList (
member_rank (	`
DungeonRecordEntry
key (	9
value (2*.DbGuildDungeonDetail.Detail.DungeonDetail:8:sÒóApiGuildDungeonRecordViewÊó'/guild-dungeon/record/<int:dungeon_id>/Úó'è·å–æŒ‡å®šå…¬ä¼šæ´»åŠ¨çš„å‰¯æœ¬è®°å½•"ï
DbGuildDungeonScoreReward
	player_id (	C
reward_record (2,.DbGuildDungeonScoreReward.RewardRecordEntry
	RewardGot

reward_got (Y
RewardRecordEntry
key (3
value (2$.DbGuildDungeonScoreReward.RewardGot:8"„
ApiGuildDungeonScoreReward

dungeon_id (
score (×
postK
reward_records (23.ApiGuildDungeonScoreReward.post.RewardRecordsEntry&

res_change (2.STResourceDynamicZ
RewardRecordsEntry
key (	3
value (2$.DbGuildDungeonScoreReward.RewardGot:8ö
getJ
reward_records (22.ApiGuildDungeonScoreReward.get.RewardRecordsEntryG
dungeon_records (2..DBGamePlayer.GuildDungeonRecord.DungeonRecordZ
RewardRecordsEntry
key (	3
value (2$.DbGuildDungeonScoreReward.RewardGot:8:pÒóApiGuildDungeonScoreRewardViewÊó/guild-dungeon/reward/Úó0å…¬ä¼šæ”¯æ´å’ŒæŒ‘æˆ˜å‰¯æœ¬çš„ç§¯åˆ†å¥–åŠ±é¢†å–"2
DbGuildDungeonProgressReward

reward_got ("™
ApiGuildDungeonProgressReward

dungeon_id (
progress (B
post

reward_got (&

res_change (2.STResourceDynamic
get

reward_got (:sÒó!ApiGuildDungeonProgressRewardViewÊó/guild-dungeon/progress-reward/Úó'å…¬ä¼šæŒ‘æˆ˜å‰¯æœ¬çš„è¿›åº¦å¥–åŠ±é¢†å–"‰
ApiGMGuildDungeon

dungeon_id (
score (
result:GÒóApiGMGuildDungeonViewÊó/guild-dungeon/gm/Úóå…¬ä¼šå‰¯æœ¬ç›¸å…³GMbproto3