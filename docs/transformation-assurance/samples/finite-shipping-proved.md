# 변환 보증 보고서 / Transformation assurance report - finite-shipping-proved

## 결과 / Verdict

**선언한 범위 안에서 기능이 유지되었습니다** / Behaviour preserved within the declared envelope (`PASS`, decided by `proved`)

## 세 가지 질문 / Three questions

### 전이랑 같아? / Same as before? - yes

- 가능한 입력 전체 80건을 모두 확인했고, 모두 같았습니다.
- 성능은 검증하지 않았습니다: 성능 측정 규약(performance_envelope 클레임)이 선언되지 않았습니다.

- Every one of the 80 possible inputs in the declared domain was compared and matched.
- Performance was NOT VERIFIED: no performance_envelope claim declared a measurement protocol and tolerance.

### 이상한 점 있어? / Anything odd? - none

- 없음. 비교한 범위에서는 달라진 곳을 찾지 못했습니다.

- None found within what was compared.

### 모르면 솔직히 말해! / What could you not check? - none

- 선언된 검사는 모두 평가했습니다. 선언되지 않은 동작은 보지 못합니다.

- Every declared check was evaluated. Behaviour outside the declaration is not observed.

## 요약 / Summary

### 기능 유지 / Behaviour preserved - yes

- 가능한 입력 전체 80건을 모두 확인했고, 모두 같았습니다.

- Every one of the 80 possible inputs in the declared domain was compared and matched.

### 성능 유지 / Performance - not_verified

- 성능은 검증하지 않았습니다: 성능 측정 규약(performance_envelope 클레임)이 선언되지 않았습니다.

- Performance was NOT VERIFIED: no performance_envelope claim declared a measurement protocol and tolerance.

### 정리 완료 항목 / Cleanup completed - not_applicable

- 해당 없음

- Not applicable: this session compares an existing before and after.

### 되돌린 변경 / Changes reverted - not_applicable

- 해당 없음

- Not applicable: this session compares an existing before and after.

### 확인하지 못한 영역 / Not verified - none

- 없음

- None: every declared check was evaluated.

### 다음에 사람이 볼 것 / For a person to look at next - none

- 없음

- None.

## Technical assurance report

### Manifest

- digest: `376d614e00ea84738c75919128795642d81ce25ab657fda6d722ecbfc58cc21d`
- history: `376d614e00ea84738c75919128795642d81ce25ab657fda6d722ecbfc58cc21d`
- schema: invara.assurance.manifest/1
- source: `<python> source.py` at `$SOURCE_ROOT`
- target: `<python> target_ok.py` at `$TARGET_ROOT`
- input domain: finite via stdin_json, corpus 0, finite cardinality 80

### Baseline

- baseline digest: `4646139a4dc92eec84efbbd6cad757712fcb351c4ffe9940da70767078def9b6`
- policy set digest: `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
- f-0001: `ac60c7685c18bfef2c0a6d89c2020d05077dcf5951a3f86c59f785752c494832`
- f-0002: `9b5395ca93d6b960c65c056cce515c0b0785dd081147c6171c8a63407fbb9886`
- f-0003: `aef5605d9028854869601c7cb504b978ecdce25961521f767b9eaa583f5d4cd0`
- f-0004: `33c1edd309fb003b02536660b7d4f23ca29367de836d03a409456bcd93539d3c`
- f-0005: `2e53db3bb1eac7b0ce89a6dc1b1fecf0d5cb927a8937bb0b84c69ae3d30bc3a7`
- f-0006: `29da8c0bca94e7651c6f872cce27ca0ba08cc9516531757a65296589d5f5c06d`
- f-0007: `6fbf2bf8f4a1ccdba7a4dca88d4c75ad252198fa4ecb221e9bc7210f68aa190b`
- f-0008: `ffb8d1b02c31e8e0b46de9c3de58d2fca5f607e9ef09f81a8ecf93f5a7a99e3f`
- f-0009: `de761a680fb300b2177012d90adccbca1e85184c3ff3a88a796569d4a1d4343b`
- f-0010: `1c94af42bf3bfee60285b8599ec25400fb298e653e5d8b0f699e750cbbcd50b6`
- f-0011: `53abb4d508699239d7e172e1c70b0abaeaa209d4b844800a1e66699227be2250`
- f-0012: `ad34a9eccc51273538012c0753411fab2cebac9756e931c8d18a26e39b019f96`
- f-0013: `121165038ac875a9ba9873ec0aba8498dc9b601fadadc409d4806e9b8a483339`
- f-0014: `224e9bc47d91a15d3fa045e114cab0bc09fadcf0fd69f442f98041d80a96ed54`
- f-0015: `b153c988776f3c12eabb4642ea7e844efed9d5d5605e3664f6439d6bc81f380c`
- f-0016: `f8f82556760097f56384b8991c56841894a43816e476ad3efd9cb4baea010069`
- f-0017: `110df4f12afaef1dde63edc2ac02543f2334e64f06741df6d89b5a89797ed4b0`
- f-0018: `c95f235d9b4726fa0937973224c5a6159a2b4a742979eb983c7d136f890d64bc`
- f-0019: `2a060d75d3cb096f914a7ba50aff6eb1a42c6be73774acbad04a154de3d58008`
- f-0020: `7fe257981aa1e9945918b64e258b5eff28b210e8eaacb88a7eaea94136c62350`
- f-0021: `75fef0778b06b612d47c84cc6c528ec3f27e3374af7a79b44920b65ad2583163`
- f-0022: `f490417fc98f0a61d7257e6a1b65b609a77b4208e5c6c39a6b76403071763135`
- f-0023: `b45e57b0bf07da6f59b56b2f10db4b04aa708d9a38f11f7769316491924bbc50`
- f-0024: `4135af4f9268cc13dee7ffa36e27f98911082127be29e0a3e886444c9a8e4ad3`
- f-0025: `1344300a6835e251a85f0c07596d8cd9f892580ceff8488b958f825c9daa4446`
- f-0026: `558ba54c6b8fb0de156a3cf3cc832c605817fc28a92a25e885114865d22f992c`
- f-0027: `80214ddcc1c7939d5cba8f7c22beb2dc6544c20ef7ab8637c6a7b1d2a2741972`
- f-0028: `940b4930c17186e339a08c31d3304c2f075c390995a505a8eeed70b8c01ffe0f`
- f-0029: `b7bf0040ab0842495bf6f80df10b4ed7b236ee30120dd097cc8beedec83d4c03`
- f-0030: `3101b65a60af4ecdd9a370eb2004e261ecdfff35ef2d43944a0645480abeef19`
- f-0031: `564a065183ff60dc473a73e7d1e60db81168e362e4ac12ac00a0bb76ab8b8eed`
- f-0032: `f8211f78d77f9231f0711cf382957724395890eb914865dd8bb588b32cfd7c3d`
- f-0033: `1efb5a219c59eb700ee4b651fffbc99da51517a132215cfd141b528061f1c108`
- f-0034: `d357f67ed535ce36f50d7a5b281144bcb86a9168119fb7f4d8f20177028f62be`
- f-0035: `4dfec97cfdccf352920683beb9c4d0921e9f01c2b452557ebda4b7e038c64678`
- f-0036: `3fbb44ff58160d7dc319ecf62e34c1c64b49ae086a985d84d3f58b05f1368a19`
- f-0037: `20e2c9daa418667033c60b1080a1bad3b86baed0c45d8c8ef169fbd4e448b6bc`
- f-0038: `eaa527c06e110dc98a0291d99a76c0b945745346006c0acf1c9fc05a27d8558e`
- f-0039: `1dad76f5e56edc92dd2c8d09bc25eb23a7380ee22677b0a6b726083b8054b2a2`
- f-0040: `12573e91a75abd2a5e51d592fc2cac333433262d0ed6b11e72e3bfb6c0a18d1d`
- f-0041: `59d1db5db3b7a38442cce1df9254f3910a29eb6b31c70e8e4de290a43aee8bc4`
- f-0042: `5f03cac0c3cfc167e0078516957474a87d832195cd45c32db11cb0734cd74086`
- f-0043: `d1296e019fe9ce589d3e1040ffcf36f9071c4a34f18f6745a1ee945b9bcefe8f`
- f-0044: `a0efec788b80c17d91a5fbab26d9f4be2f905aa765ae2834ff5102ddfcad4e99`
- f-0045: `ee3210b944d7ebf51d40f73d2e837e81d802ffe5d3844327de45b3086a31b291`
- f-0046: `2e5031316f054383198705b6701c3934801ab6ecb0cb801113d3d5a47b6b7172`
- f-0047: `b30da7d43524fe01cf639b3c76f9014064070195c3922c529c9da40f8bc9caa8`
- f-0048: `8def03efb452cb1916ed782c0f0a01a8a3fcbdef546f368b276f3c3cc3d467b8`
- f-0049: `6c7bd4876916ac67a8c99bdc91ff2dd13ff3b89ac88a06ad992640582995479e`
- f-0050: `dbae7a5b083d1410a264c9803740b2ed89ad15f04e24fda68df5ac938f487266`
- f-0051: `d1a2af7010b9531e7b9b10086efffcb094237229a55bec6b146d096fd5131ccf`
- f-0052: `357f5019216c5c278c00438c75d7e0a4ed8b8b6a43842bc519021f39ab9ce4c9`
- f-0053: `8f49b4bcbc6949eb3f113039b4a8d749df723d6b25af67755242bd7cbc807fef`
- f-0054: `95b18192f8a7d1d0a8b0d6ea875a51078abfe5cd540b680103d49ea77fbe9298`
- f-0055: `efdcdb31c5f614dda48cf18b4aa6c874f81c0ad6f432d74a288f4d9b5c38dfd5`
- f-0056: `f6e62559e44c29ad45fea745501332a5a06ef0eb09e8325d7071b485ff1d82db`
- f-0057: `34d080ae29eaee283c8f5e29814835e20bc1f505e3258f008c44221c1db07d52`
- f-0058: `1170473170d68c56d9ceb9efbd89277b5c00ceff24dae3d16856d42ad30891ac`
- f-0059: `b8f804d72b4dd888ec6f2de022d4751fcdccd725cf4e7ebbc0e2e13afb299514`
- f-0060: `df2e46d7f58ab66d298d5f5d3380344db41747469281cd93811671b0277b8408`
- f-0061: `bd1b8204a9cda90bcaaeb605e0a52b88aa33c7cb8634dc7e55fca2e7a2722714`
- f-0062: `269481355ea196c1cbc5a2ba62244c669d68ee1966b1d9c7b076d28d4ab58a7d`
- f-0063: `6c25bb752fcdf3431a28e1afb920f424bfc4f66c6539b0fc8e8211da17234adf`
- f-0064: `b2221612d44af2b93c92e12e93882f1666074c9eb2750a2d91ed52d33c03e0c5`
- f-0065: `ecf43994fc9fd1969f7011db954a802d2f039ece157cff11e2edfe1f5e088bb6`
- f-0066: `b4d26d0cb6ee148f804cedac3271500e9a81a54868ae8e3f6325c5d662b2a2c5`
- f-0067: `696c174db0601191660e757032ef1f147dcbf4596dad3d4811eb4933de97664f`
- f-0068: `478a1b3c40f85c4fbac558cb894d25940e0872021ceeb216ac4e3d21a3311bff`
- f-0069: `4f71eb5ce3104df715ecf27ba27fd6dff44706e6cad6805c71b61e417dc5d45a`
- f-0070: `27db7e22b5adc84e018a3f809987a96dd64f6ecd4bd623cf1be1392ca49381a1`
- f-0071: `2c4e163d4853c5bda4297dd44b694622bb12a36fc3459d748ccc757c68013e83`
- f-0072: `cbb719e9c29de61119556cab7029694bf13ba6434e551d2c81cf3cc8c6e868ae`
- f-0073: `2b544f873402222578d7bc757bb0d7ffaf16810ea57be75c3dfa9f7af3e7f37a`
- f-0074: `a551b3a04d33279b7fe41f5c504fcbabc3338615564b2fa3060798bf4e6d6c2c`
- f-0075: `9f56dbf9d212d82f62b08084d208c501290fd8baa35f200a9d25f33cbd2a6dc5`
- f-0076: `caaf22e168919fa754cb06ade1f1cc95cb3d01e1747a9a664a62971315065c9c`
- f-0077: `7c220afda5687376ed756db9d4c2aed91bc23713d756d289d1f87fd788f2bb4d`
- f-0078: `5c794be8d217650e3bd8859ad92e065a0f35ab54511fd457687ef79c4ef6d27e`
- f-0079: `2bda7aafd441377c2b800ab72e55b0b868a4aef72557af527c40e074dc36e674`
- f-0080: `082b60126bbf3ed8e3414ff03dc8387d326e9bc0e3b3797bc4d1aa6d39b52641`

### Policies

- exact comparison everywhere

### Coverage

- exhaustively_proved: domain
- tested_over_finite_corpus: (none)
- searched_without_divergence: (none)
- diverged: (none)
- not_verified: (none)
- needs_human: (none)
- explicitly_excluded: (none)
- not_observed: (none)

### Coverage map

- All 2 observed values were compared (2 proved, 0 tested on every input, 0 searched only).
- by state: PROVED 2
- probe `cli` (process, mandatory): PROVED (PROVED 1)
- probe `out` (json, mandatory): PROVED (PROVED 1)
- claim `domain` (finite_domain_proof): PROVED

### Blind-spot scan

- 2 value path(s) over 80 input(s), 160 of 160 site(s) mutated under the manifest in force; 2 sensitive, 0 blind, 0 replaced by a declared placeholder, 0 dulled, 0 fail closed
- boundary: tried 160, noticed 160, missed 0, unverifiable 0
- null: tried 160, noticed 160, missed 0, unverifiable 0
- type_flip: tried 160, noticed 160, missed 0, unverifiable 0
- synthetic mutations of the frozen baseline under the manifest in force; a blind spot is a value no tested change would have flagged; a placeholder is a value the declaration replaces, where another value of the same shape passes by design; this measures the equivalence definition, not the transformation

### Claims

- `domain` (finite_domain_proof, mandatory): **PROVED_WITHIN_DECLARED_DOMAIN** - all 80 member(s) of the declared domain compared equivalent

### Divergences

- none

### Counterexamples

- none

### Repair units

- none

### Findings and metrics

- none recorded

### Amendments

- none

### Provenance

- repository: None
- base_commit: None
- accepted_commit: None
- workspace: None
- roots: {'SOURCE_ROOT': '<fixture-root>\\finite', 'TARGET_ROOT': '<fixture-root>\\finite'}
- manifest_provenance: {}
- tool_versions: {'python': '3.12.10', 'invara': '0.1.3', 'git': 'see provenance.git'}
- platform: Windows-11-10.0.26200-SP0
- evidence_store: <sandbox>\finite-shipping-proved.db
- evidence_chain_heads: {'assurance_manifest': 'c2c0a1b5e0f93a599c777862d68e5b7b8d19d1b3b31540aff3e3bcf11a9cf7e2', 'assurance_observation': '62aab6e8fb0a978bacfec77d9b67ebf470731f4ae961034f165cdc47d0441e63', 'assurance_event': 'fd852a4d8b8e05cf77b2bef52bfc31787f0e8cc67a71c95c8fcaf318cdcee2ef'}

### Final verdict

- `PASS` decided by `proved`: 1 mandatory claim(s) proved within the declared domain
- proved: domain

### Limitations

- Equivalence is decided only within the declared input domain and the declared probes; behaviour outside them is not observed.
- Only an explicitly finite domain is proved by exhaustion; corpus comparison and counterexample search are finite evidence, not proofs.
- Thread and process scheduling, wall-clock time and hardware randomness are not controlled; their effects are handled only through declared policies.
- External services are outside the verification boundary; any request to a non-loopback host makes the run unverifiable.
- Non-exact comparison policies relax equality where the manifest says so; each application is logged, and the policy set is part of every digest.
- Structural metrics are measured only for Python sources; findings for other languages are declared by the host agent and recorded as declarations.
- A repair unit is accepted from evidence about the verified tree; INVARA does not judge whether the engineering intent was met.
