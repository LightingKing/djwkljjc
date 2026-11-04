"""Execute native table/skill consumers against candidate additive role data."""
import json
import sys
from pathlib import Path
import unittest
from lupa.lua53 import LuaRuntime
from external_assets import external_path, require_file

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / 'client-patch/hot-update/sources'
MAIN = (1503, 1505, 1506, 1511, 1515, 1516, 1601, 1607)
PRESENTABLE = (1501, 1503, 1505, 1506, 1511, 1515, 1516, 1517, 1601, 1607, 2016, 2017, 2018, 2106, 2115)


class RoleCompletionClientTests(unittest.TestCase):
    def setUp(self):
        self.lua_root = external_path('LJJC_DECOMPILED_LUA', 'analysis/lua-decompiled')
        require_file(self.lua_root / 'xlsx__actorskill.lua', 'LJJC_DECOMPILED_LUA')
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute('''
            function ProtobufI(name,row)
                return setmetatable(row, {__index=function(_,k)
                    if k=='displayIndex' or k=='resIndex' or k=='costIndex' then return 0 end
                    if k=='isPassive' or k=='isLink' then return false end
                end})
            end
            PB={}; Tables={}; Indexed={}
            PB.all=function(n) return Tables[n] end
            PB.get=function(n,...) local i=Indexed[n]; for _,k in ipairs({...}) do i=i and i[k] end
                return i and Tables[n][i] end
            _ENV['!']=function(t) return t end
            WU={}; SE={}
        ''')

    def table(self, name):
        candidate = SOURCES / f'{name.lower()}-role-completion.lua'
        original = self.lua_root / f'xlsx__{name.lower()}.lua'
        data = self.lua.execute((candidate if candidate.exists() else original).read_text(encoding='utf-8'))
        self.lua.globals().Tables[name] = data[1]
        self.lua.globals().Indexed[name] = data[2]
        return data[1]

    def test_native_skill_groups_and_combo_costs_have_no_missing_slots(self):
        self.table('ActorSkill'); self.table('ActorCombo')
        text = (self.lua_root / 'common__skilleffect.lua').read_text(encoding='utf-8')
        fn = text[text.index('function SE.GetActorSkills('):text.index('function SE.GetSkillCount(')]
        self.lua.execute(fn)
        for actor_id in MAIN:
            groups = self.lua.globals().SE.GetActorSkills(actor_id, True)
            for slot in (1, 2, 3):
                with self.subTest(actor=actor_id, slot=slot):
                    self.assertIsNotNone(groups[slot])
                    row = groups[slot][1]
                    self.assertTrue(row['cmdStr'])
                    combo = self.lua.globals().PB.get('ActorCombo', actor_id, slot if slot<3 else 0, 0 if slot<3 else 1)
                    self.assertIsNotNone(combo)
                    self.assertIn(row['skillId'], list(combo['skillList'].values()))

    def test_original_detail_and_get_name_tag_expression_no_longer_indexes_nil(self):
        self.table('ActorTag')
        # These are the native ActorMain:276 / NewActorGet:213 tag-key expressions.
        make_tag = self.lua.eval('function(id) local actorTag=PB.get("ActorTag",id); return "tag" .. actorTag.tag[1] end')
        for actor_id in PRESENTABLE:
            with self.subTest(actor=actor_id):
                self.assertRegex(make_tag(actor_id), '^tag[0-9]+$')

    def test_experimental_satan_third_slot_binds_its_own_action_name(self):
        self.table('ActionDamage')
        row=self.lua.globals().PB.get('ActionDamage',1607003,'sup101')
        self.assertIsNotNone(row)
        self.assertEqual(row['index'],210700201)

    def test_candidate_names_keep_existing_locale_and_sprite_when_present(self):
        candidate=SOURCES/'windowutil-role-completion.lua'
        self.assertTrue(candidate.is_file())
        self.lua.execute('''
            Existing={}; LocalServer=true; CS={UIWindowHelp={},DataBindingManager={Instance={}},LocaleManager={Instance={}}}
            CS.LocaleManager.Instance.GetString=function(_,key) return Existing[key] end
            CS.LocaleManager.Instance.GetStringFormat=function(_,key,...) return Existing[key] end
            PB.enum={LockInputFlag={}}
            _ENV['$']=function(target) return target end
            function require(n)
                if n=='Common/PbHelper' then return PB end
                if n=='Common/Singleton' then return {Get=function() return {IsLocalAccountServer=function() return LocalServer end} end} end
                return {}
            end
        ''')
        factory=self.lua.execute(candidate.read_text(encoding='utf-8'))
        wu=factory()[0]
        for actor_id in PRESENTABLE+(2134,):
            with self.subTest(actor=actor_id):
                self.assertTrue(wu.GetString(f'ActorName_{actor_id}'))
        self.lua.globals().Existing['ActorName_1501']='original retained'
        self.assertEqual(wu.GetString('ActorName_1501'),'original retained')
        sprite=self.lua.eval('{spriteName="actor_name_1501a", GetAtlasSprite=function() return nil end}')
        wu.CompleteRoleNameSprite(sprite)
        self.assertEqual(sprite.spriteName,'actor_name_1001a')
        sprite=self.lua.eval('{spriteName="actor_name_1501a", GetAtlasSprite=function() return {} end}')
        wu.CompleteRoleNameSprite(sprite)
        self.assertEqual(sprite.spriteName,'actor_name_1501a')
        self.lua.globals().Existing['SkillName_1003001']='donor active skill'
        self.assertEqual(wu.GetString('SkillName_1503001'),'donor active skill')
        self.lua.globals().Existing['SkillDescribe_1003001']='original donor effect description'
        self.assertEqual(wu.GetString('SkillDescribe_1503001'),'original donor effect description')
        self.lua.globals().LocalServer=False
        self.assertIsNone(wu.GetString('ActorName_1503'))

    def test_new_skill_icon_resolves_to_exact_existing_same_role_resource(self):
        candidate=SOURCES/'skilleffect-role-completion.lua'
        source=(candidate if candidate.exists() else self.lua_root/'common__skilleffect.lua').read_text(encoding='utf-8')
        self.lua.execute('WU={IsLocalContentServer=function() return true end}; function require(n) if n=="Common/WindowUtil" then return function() return WU,{} end end return {} end')
        se=self.lua.execute(source)
        row=self.lua.table_from({'skillId':1503001,'actorId':1503,'displayIndex':1,'resIndex':1})
        path=se.GetSkillTexturePath(row,1503)
        self.assertEqual(path,'Texture/ActorSkillBig/actor_skill_100301')
        root=external_path('LJJC_ASSET_BUNDLES','analysis/ljjc_manifest_out/assets/Patch/assetbundle')
        self.assertTrue((root/path.lower()).is_file())

    def test_name_sprite_branch_is_present_in_native_list_detail_get_modules(self):
        for name in ('actormain','newactorget','widgetactorcard'):
            candidate=SOURCES/f'{name}-role-completion.lua'
            with self.subTest(module=name):
                self.assertTrue(candidate.is_file())
                source=candidate.read_text(encoding='utf-8')
                self.assertIn('WU.CompleteRoleNameSprite(REF.SpriteActorName.UISprite)',source)
                self.lua.compile(source)

    def test_every_candidate_lua_compiles_roundtrips_fci_and_loads(self):
        sys.path.insert(0, str(ROOT/'client-patch/hot-update'))
        from fci_lua_codec import standard_to_fci, fci_to_standard
        runtime=LuaRuntime(encoding=None)
        compile_lua=runtime.eval(b'function(s) return string.dump(assert(load(s)),false) end')
        for path in sorted(SOURCES.glob('*-role-completion.lua')):
            with self.subTest(module=path.name):
                standard=compile_lua(path.read_bytes())
                decoded=fci_to_standard(standard_to_fci(standard))
                self.assertEqual(decoded, standard)
                self.assertTrue(runtime.eval(b'function(s) return load(s) ~= nil end')(decoded))

    def test_all_banner_fallbacks_exist_in_the_real_native_name_atlases(self):
        import UnityPy
        root=external_path('LJJC_ASSET_BUNDLES','analysis/ljjc_manifest_out/assets/Patch/assetbundle')
        policy=json.loads((ROOT/'data/server-only/role-completion-20261104.json').read_text(encoding='utf-8'))
        configs={r['id']:r for r in json.loads((ROOT/'data/client-tables/ActorConfig.json').read_text(encoding='utf-8'))['rows']}
        for atlas in ('actorlistname','actorlistname_l','actorlistnamevertical','actorlistnameoutline'):
            path=require_file(root/'dependencies/atlas'/atlas,'LJJC_ASSET_BUNDLES')
            names=set()
            for obj in UnityPy.load(str(path)).objects:
                if obj.type.name=='MonoBehaviour':
                    names.update(s['name'] for s in obj.read_typetree().get('mSprites',[]))
            for target,donor in policy['sameRoleDonors'].items():
                with self.subTest(atlas=atlas,actor=target):
                    self.assertEqual(configs[int(target)]['role'],configs[donor]['role'])
                    self.assertIn('actor_name_'+configs[donor]['animRes'],names)

    def test_real_command_targets_exist_in_each_selected_model_animation_bundle(self):
        import UnityPy
        from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
        import systems
        descriptor=external_path('LJJC_ACTOR_LOGIC_SCHEMA', 'analysis/ljjc-local/login-schema/raw/004_actor_logic_data_proto.proto.pb')
        require_file(descriptor, 'LJJC_ACTOR_LOGIC_SCHEMA')
        proto=descriptor_pb2.FileDescriptorProto.FromString(descriptor.read_bytes())
        pool=descriptor_pool.DescriptorPool();pool.Add(proto)
        cls=message_factory.GetMessageClass(pool.FindMessageTypeByName('ActorLogicDataProto'))
        root=external_path('LJJC_ASSET_BUNDLES','analysis/ljjc_manifest_out/assets/Patch/assetbundle')
        for actor_id in MAIN+(2115,):
            config=systems.ACTOR_CONFIG[actor_id]
            model=config['prefabRes']
            path=require_file(root/'data/logicproto'/config['logicRes'],'LJJC_ASSET_BUNDLES')
            raw=next(o.read().m_Script.encode('utf-8','surrogateescape')
                     for o in UnityPy.load(str(path)).objects if o.type.name=='TextAsset')
            self.assertEqual(raw[:4],b'FCI\0')
            # Read-only FCI decoding: known protobuf aniXml field for this model.
            cipher=raw[4:];known=bytes((10,len(model)))+model.encode()
            key=[cipher[j]^known[j] for j in range(4)]
            logic=cls.FromString(bytes(c^key[(i%4+i//4)%4] for i,c in enumerate(cipher)))
            self.assertEqual(logic.aniXml,model)
            anim=require_file(root/'animation/role'/model,'LJJC_ASSET_BUNDLES')
            names={o.read().m_Name for o in UnityPy.load(str(anim)).objects
                   if o.type.name in ('GameObject','AnimationClip')}
            for row in systems.ACTOR_SKILL_ROWS.values():
                if row.get('actorId')!=actor_id or row.get('isPassive') or row.get('isLink') or row.get('displayIndex',0) not in (1,2,3):
                    continue
                with self.subTest(actor=actor_id,skill=row['skillId']):
                    command=int(row['cmdStr'].removeprefix('InputCardCmd'))
                    actions=[a for a in logic.action if command in a.inputKey]
                    self.assertTrue(actions)
                    for action in actions:
                        self.assertIn(model+'_'+(action.animate or action.name),names)
        # This proves command/animation references only, NOT a successful battle.
        policy=json.loads((ROOT/'data/server-only/role-completion-20261104.json').read_text(encoding='utf-8'))
        self.assertIn(1607,policy['experimentalMainModelIds'])
        self.assertEqual(policy['battleAcceptance'],'pending isolated emulator-5566 for every actor; no automatic approval')
