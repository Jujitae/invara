# 변환 보증 보고서 / Transformation assurance report - finite-shipping-diverged

## 결과 / Verdict

**기능이 달라졌습니다. 이 변경은 반영할 수 없습니다** / Behaviour changed; the transformation is not accepted (`BLOCK`, decided by `diverged`)

## 세 가지 질문 / Three questions

### 전이랑 같아? / Same as before? - no

- 결과가 달라진 곳: input f-0080: /out/value/cost (37.5 -> 38)
- 차이를 재현하는 가장 작은 입력: {'express': True, 'weight_class': 8, 'zone': 5}
- 성능은 검증하지 않았습니다: 성능 측정 규약(performance_envelope 클레임)이 선언되지 않았습니다.

- Behaviour differed: input f-0080: /out/value/cost (37.5 -> 38) (values differ (37.5 vs 38.0))
- Smallest input that reproduces the difference: {'express': True, 'weight_class': 8, 'zone': 5}
- Performance was NOT VERIFIED: no performance_envelope claim declared a measurement protocol and tolerance.

### 이상한 점 있어? / Anything odd? - listed

- 결과가 달라진 곳: input f-0080: /out/value/cost (37.5 -> 38)
- 차이를 재현하는 가장 작은 입력: {'express': True, 'weight_class': 8, 'zone': 5}

- Behaviour differed: input f-0080: /out/value/cost (37.5 -> 38) (values differ (37.5 vs 38.0))
- Smallest input that reproduces the difference: {'express': True, 'weight_class': 8, 'zone': 5}

### 모르면 솔직히 말해! / What could you not check? - none

- 선언된 검사는 모두 평가했습니다. 선언되지 않은 동작은 보지 못합니다.

- Every declared check was evaluated. Behaviour outside the declaration is not observed.

## 요약 / Summary

### 기능 유지 / Behaviour preserved - no

- 결과가 달라진 곳: input f-0080: /out/value/cost (37.5 -> 38)
- 차이를 재현하는 가장 작은 입력: {'express': True, 'weight_class': 8, 'zone': 5}

- Behaviour differed: input f-0080: /out/value/cost (37.5 -> 38) (values differ (37.5 vs 38.0))
- Smallest input that reproduces the difference: {'express': True, 'weight_class': 8, 'zone': 5}

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

- digest: `4b29841037ecd09cdbec7931243707272cf1ba78ae413106d4663c27f39e64db`
- history: `4b29841037ecd09cdbec7931243707272cf1ba78ae413106d4663c27f39e64db`
- schema: invara.assurance.manifest/1
- source: `<python> source.py` at `$SOURCE_ROOT`
- target: `<python> target_bad.py` at `$TARGET_ROOT`
- input domain: finite via stdin_json, corpus 0, finite cardinality 80

### Baseline

- baseline digest: `e2e13117b4b47741942c04bdfdbe09f8b1cc340d4e9528c5ed63cd412c27f59e`
- policy set digest: `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
- f-0001: `9f45a5797dec286a1baec745def42455cbaeaa545591846b1e2d453c7d4a2b2c`
- f-0002: `f75b07fbccbda4b03136155fdbadb92242b408655a4653de0019533e80603dd3`
- f-0003: `38266948fe3066a64efd08986557d7d75afca73674f16a5aa5c8b0eac0d1e969`
- f-0004: `9f752ca69cc5a13ca92961f04efd10b60b7b6db1f7b5cbb8ccb118f73e6637bc`
- f-0005: `c66ea57f2ff6a1c7c8dbb5ee594268152c8c917d4ae5ad1fad82ac41fdc0ee9e`
- f-0006: `3cb90dc330c0e45baf2b764002bb5dce733baa9fe27e44dedcbe04605b602614`
- f-0007: `a8c4f8e19913adc65d6f2f41eb868ef19bd002cee63f7739c25f5e111dc0d8ff`
- f-0008: `e47057ca936595b4c792fc90436e0a8bc2502f9a5014e05650d3cbe97b949992`
- f-0009: `385498f2bb74875bf0971b2e8314bd4ce3682dae5ae7dff1f2517f63ab921604`
- f-0010: `f78e4fc9cc26f3d76867b02ac0215f5133eb9d766e4b5b1d51f7d796768bf86e`
- f-0011: `4820d1c1e532d00810bdd2444b269214995aeda5b3b69ffe2f1b6b7f2188c8c2`
- f-0012: `fea15f1373b02959adc0b9d4ff78f748f54a431bc704dc443f655c138f091337`
- f-0013: `fe95693b184630f06bb1e331355084d1e580dd6ff01bd02e99666e33d8a03b09`
- f-0014: `6af27fd9d8fd26ebad36e1185bf4c7c65337778fbca0ee0bf63621a55510c69b`
- f-0015: `2520401b35c1d0402d6fd4453b5b9db1eadc1f3cb31181a381a6162310a564e0`
- f-0016: `f5c186d53593693131795d497d50028c34b8b656eee1126a9196ee133cb2b419`
- f-0017: `9e551c28b3f668553efd4741739d49349235df3a5f0f8e9845c80ee31dadafd1`
- f-0018: `ff9e452b6250ae7a11336662afa0adce320e51f7df56bfafda4fe6836e212e91`
- f-0019: `82792c7d360f1ffdfe204086e87f8be8ab83b19a5eac77e5f08bd64b5ad46747`
- f-0020: `8762b12be9a5844709d48baa0b9674905dbec84746f04883329ae6df495b0382`
- f-0021: `fa784e43a7b975d8adecfee6846710af84cb4b401e86df57012680efb0dd6c4b`
- f-0022: `e66a20cd2f44ec4ff17cffb48805fea05dba40c09996bb7dea0c191fe64c3f58`
- f-0023: `ef0d6f2a69f99de29aecdd013c71986039c0e7b76eaf19d7866692590b5e5df4`
- f-0024: `4cb5ecd58d197bbe8ca524c90ebb032ad51d63d2e7f3f66c652e7ed769ac714e`
- f-0025: `1de71574f20dcbd119cd10cc77b2ed54283d467b0c1f3a957d63ebcc2fa6d7da`
- f-0026: `97a7719614f463aaf14d5f1b5d17466883195c6a5677a251364c660be3227948`
- f-0027: `f33a99168bbe49dcdff4d06a54887dc0f856c57c1c45d6583887941849a8fa6d`
- f-0028: `d666d4b0d881c52521019c805d426182f0e93332871b59739d70ea5cd7255dab`
- f-0029: `b8b68f80e3be40adb6f1949061bb41a26ae75dbb45936b41d4a27e420e561ee0`
- f-0030: `0eccbe4baa9fb469ea104b13f5af428600fec7ba9db1913a07226d4da1100ae6`
- f-0031: `7a210c243e547e95d84bb1874847a6b4e32d84c8ed05edbabea49607cc8785a6`
- f-0032: `deca9d49dae1f12285ce2ba25cf7374dc3b9a4e39e9041987b7d3a6e2169b9e6`
- f-0033: `8305ac66b47b184a26421bb1c102ad28d525266aeee68178614267eeff75fa56`
- f-0034: `6802821aca3fd286e1afc778ba425261c5426b820edb32c6764a3c573c7c18df`
- f-0035: `a5e1dc00bae828f90e984a2473c02d786320b72f18c1c9094cdf872c3b974bf1`
- f-0036: `1ec4c3c476f001214d1cd211022f810efdbf03c6fc2cd4f004fa2a4b40552502`
- f-0037: `a323c36edec8dc7e4d97acbd4e7401f4790cb41966a282329e20b448b1b8f765`
- f-0038: `cb4206af906bbab6465e18524f3483742cd10615897c2b0d4319a6e3c88fe7d7`
- f-0039: `0de428ae9e2c620a1fbcb9f7c168978add09a48abf64812219852cce6596b6bc`
- f-0040: `63aa1ba3dff4c9e656649966b61ed9c6ee6e7ec5e55a12b12f7627fda8fa2fda`
- f-0041: `71486496799b35cbf35390db804cbe00c0bd52d51b0533af42a28560e9cd63a1`
- f-0042: `e19f48cba055f5141670780eee9315449f6be188f84f74c8d8e963833ae48c5c`
- f-0043: `b71503c1ae6606f219e58e5bfd3ba7c86bfa393c5a217b8e683a9e8fb37516af`
- f-0044: `1aa3ef39e6c5e97fac808da38cf40900afd5178ba5429274337cec7efa935d3b`
- f-0045: `4b9c2f4b873a41945789260241b333ed9e1bc58c32be0166a1c421179a5291c8`
- f-0046: `1694b7ce3a3f1083ac5ccc3157d39040ff7574c35ce4d72d179733d636f429ea`
- f-0047: `a036ab7676af0df6cfc67879e7843f8f55960259367884dac5f82dac763bbd39`
- f-0048: `c7d6a370e63294956ef3f436e4ef1bafb95b4fcf8f74e743e922787cade10d45`
- f-0049: `aeab38d88310bc19c7295e712c6c681d11988a2279067dee9356fc12ebb73bc6`
- f-0050: `c8c72dd96c2cad88597c2af845ca1b938abc0e62a26361afa71bce257f30736e`
- f-0051: `a1fad04f3c39ad7ce39d973b2d43bbbf5126dfd2bad59dd7dd2b5350d7acd81c`
- f-0052: `ce88d3d4baee78e32f508ef19f3afbe51095657e7bd02db68bccb65b2f3b8cb5`
- f-0053: `fcd33ae1dc94c41a292babe96ca2c6be81865ca137ec9709c59b8fae308c7cbb`
- f-0054: `a90fdfde21b517b9d864acd72230ce391230d1d9f343422e4c6f24e041014010`
- f-0055: `04889ba5da6a6616dfb9e2e1b184c254c212225b99972285b3dd5a0092d1ca2c`
- f-0056: `964797a2787423410154a0a2f61547eb874a967f103c4b28da9fe877747e1e29`
- f-0057: `4826930f15d5d7717150a2b129ae71dd2a5596c73142390906a0160737674369`
- f-0058: `ab9f6afe3b62fc74196bdedd5e588b6dd41b6cde0cf8c7b6f36fbb61db9e2ad2`
- f-0059: `959b7cfe5e0214ff23bf38f690bd25660ed9d63b2ea8803636a024f4c6a579e7`
- f-0060: `a03358c154cf632d82a3840e36f8f4e4981221a633b7ed6d9f8c55c20b5ae407`
- f-0061: `c245ccf0d4106bdf0a13b934ea804a58b6d7e572ef57956316ba52b28dd1afaa`
- f-0062: `412ef2a4fa3f9c123de5696fd947c5bfb73cee4e9b0d827add248731a6425fa2`
- f-0063: `42ed5df77f73b1f4bb55d47cc266c21552a4a1142409f2977f806b9d63029f42`
- f-0064: `8d1edceb1637b4217399005a50abd38c64c51be6684effaec06c96963b5519b3`
- f-0065: `e99b92e917758dddd29aad49cd2d7425256c6b797060e6e0540420d7b6d10b43`
- f-0066: `b4c8d44cc77015a70bb2b3db4bf7f48064f8a2e7145a66b9cc0118811e18ea44`
- f-0067: `72cdf472335b2746306bfdd64ea73e285a337190d704128cbd6397f176ae0415`
- f-0068: `715d9b496d2b7bb569a9833d474f5fc33ba4ef8370e7a38352a384a54f3b13b6`
- f-0069: `2dd9eb19c639bc69cc0d459e506b7f324b5c9f143dd3434585097d7b39bf44a0`
- f-0070: `a830e95636ca658d8a17eb2790d0424287db7ccb5212961c70a9ffa3fc2defa4`
- f-0071: `0bca9e7920b548b0bef3983882523501dcffa579860d1e6409ebb4f8ff70fe72`
- f-0072: `690b949642af027d31ae6cc0ade14b0a8bbddc00b95e103703c0a8b27cd90dbc`
- f-0073: `ef1b24ca12b3e8d8fc4d89bf24e81c2b7d520bbff8e59be45ec029809dd9c6ae`
- f-0074: `a50c68291ee870274031c60c26c1e0348c6375d149c1863bfb82c8e6afde3c9c`
- f-0075: `3a540f2a1fc0a4bbbcbb3affd26eb81ee71dddbfba2c920ea77d266a5c7beec4`
- f-0076: `678680844e5acf753d3dafbac0da40cd738458d4141da0b482ab574c68bfb666`
- f-0077: `792e840d3d905a5269e07b0eddcb81af2941e9769395f59f6c8368898dc8ce3d`
- f-0078: `3c8d5994ae1940d4600f288a4c5a93232ae57fa212fbf1f917357ca6be2e6630`
- f-0079: `c149aff493d171cb6bbc2306a7943869f2b851265752d683198ed4362fa4f286`
- f-0080: `af6c1e6e4a356ad1e2cf9da87bbadd66853679cc86208f0187015f91dd1e9bf1`

### Policies

- exact comparison everywhere

### Coverage

- exhaustively_proved: (none)
- tested_over_finite_corpus: (none)
- searched_without_divergence: (none)
- diverged: domain
- not_verified: (none)
- needs_human: (none)
- explicitly_excluded: (none)
- not_observed: (none)

### Coverage map

- 1 of 2 observed values were compared; not compared: 1 diverged.
- by state: TESTED 1, DIVERGED 1
- probe `cli` (process, mandatory): TESTED (TESTED 1)
- probe `out` (json, mandatory): DIVERGED (DIVERGED 1)
  - `/out/value/cost`: DIVERGED (a mandatory divergence was recorded here (input f-0080))
- claim `domain` (finite_domain_proof): DIVERGED

### Blind-spot scan

- 2 value path(s) over 80 input(s), 160 of 160 site(s) mutated under the manifest in force; 2 sensitive, 0 blind, 0 replaced by a declared placeholder, 0 dulled, 0 fail closed
- boundary: tried 160, noticed 160, missed 0, unverifiable 0
- null: tried 160, noticed 160, missed 0, unverifiable 0
- type_flip: tried 160, noticed 160, missed 0, unverifiable 0
- synthetic mutations of the frozen baseline under the manifest in force; a blind spot is a value no tested change would have flagged; a placeholder is a value the declaration replaces, where another value of the same shape passes by design; this measures the equivalence definition, not the transformation

### Claims

- `domain` (finite_domain_proof, mandatory): **DIVERGED** - 2 of 80 compared member(s) diverged; minimal counterexample f-0080

### Divergences

- `/out/value/cost` [domain, mandatory] raw 37.5 -> 38.0; normalized 37.5 -> 38.0; policy exact (exact); values differ (37.5 vs 38.0)

### Counterexamples

- {'diverging_count': 2, 'minimized': {'id': 'f-0080', 'input': {'express': True, 'weight_class': 8, 'zone': 5}}}

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
- evidence_store: <sandbox>\finite-shipping-diverged.db
- evidence_chain_heads: {'assurance_manifest': 'f951d3844347015273614e800a53e717db7131abf120a5f605a9c174064b91d2', 'assurance_observation': '65b85679532b2d03f984f898b14a61d9807b6ef42c5c6417fe4475676bb88e65', 'assurance_event': 'ac4de4c33a878f331dab84fd85fa644c293edb623cdc4521ba5bff022d058dff'}

### Final verdict

- `BLOCK` decided by `diverged`: 1 mandatory claim(s) diverged: domain: 1 divergence(s) recorded, first at /out/value/cost
- diverged: domain: 1 divergence(s) recorded, first at /out/value/cost

### Limitations

- Equivalence is decided only within the declared input domain and the declared probes; behaviour outside them is not observed.
- Only an explicitly finite domain is proved by exhaustion; corpus comparison and counterexample search are finite evidence, not proofs.
- Thread and process scheduling, wall-clock time and hardware randomness are not controlled; their effects are handled only through declared policies.
- External services are outside the verification boundary; any request to a non-loopback host makes the run unverifiable.
- Non-exact comparison policies relax equality where the manifest says so; each application is logged, and the policy set is part of every digest.
- Structural metrics are measured only for Python sources; findings for other languages are declared by the host agent and recorded as declarations.
- A repair unit is accepted from evidence about the verified tree; INVARA does not judge whether the engineering intent was met.
