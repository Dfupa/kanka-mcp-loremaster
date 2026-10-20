# Duncan and Éldan: synthetic session-note ground truth

Source: [duncan_eldan_noisy.txt](duncan_eldan_noisy.txt), UTF-8, Spanish.
This reference is the evaluator's answer key, not an additional input note.
All scenes, session dates and dialogue are invented for this development test.
They are not observations from Kanka or a claim that the current campaign matches.
The user's requested facts govern this fixture; later API evidence must be
reported separately, including any disagreement. No live write is authorized here.

## Authoritative fixture facts

| Subject | Ground truth | Expected treatment |
| --- | --- | --- |
| Duncan | 26 years old; an orphan | Narration explicitly confirms both; the age is not inferred from dialogue. Do not invent parents, their deaths, a birthday or an orphanage. |
| Duncan's family | Not a member of Torno; `Torn` is an explicitly identified misspelling of Torno | Do not create a second family called Torn. If Kanka has a Torno link, flag the conflict and propose only the exact scoped correction for review. Do not clear unrelated family links or infer that all orphans have no family memberships. |
| Duncan and Éldan | Both members of the Gremio de Ladrones / Guild of Thieves | Resolve a real organization by identity, or propose a missing organization after complete visible search. Membership is not a tag or a generic friendship relation. |
| Duncan and Éldan | Neither belongs to the Gremio de Exploradores / Guild of Explorers | The borrowed invitation is a disguise. Do not propose Explorers membership, create that organization merely from the negated mention, or remove an existing link automatically. |
| Lord Bone | Owner of the mansion compound | No confirmed species, age, motives, family or combat encounter. The vampire suggestion is Éldan's suspicion. |
| The mansions | One mansion compound owned by Lord Bone | One hidden maze beneath the compound; do not create a maze per mansion. Exact Kanka location names and hierarchy still require resolution. |
| Gema Sagrada / Sacred Gem | One item, with both names explicitly identifying the same gem | Not two items. No magical powers are demonstrated. Its name does not prove a mechanical effect. |
| Final possession | Éldan carries the single intact gem in his own bag | Duncan helps with the escape but never receives the gem. No sale, delivery, reward or demonstrated power. Inventory changes require exact owner/item evidence and supported separate approval. |

The prior development chat included age 45 and Torno assignment tests for Duncan.
Those are historical test context, not a verified current API baseline. If later
GET results still expose them, show the fixture's correction alongside those
records; do not silently reinterpret the source or overwrite Kanka.

## Alias resolution

| Source form | Intended referent |
| --- | --- |
| `D`, `Dun` | Duncan |
| `E`, `Eldan` | Éldan; the missing accent is explicitly explained in the source glossary |
| `LB` | Lord Bone |
| `GL`, `Guild of Thieves` | Gremio de Ladrones |
| `GE`, `Guild of Explorers` | Gremio de Exploradores, mentioned negatively |
| `Sacred Gem` | Gema Sagrada |
| `Torn` | Torno, an explicitly identified mistaken spelling |

These bindings are source-backed fixture meanings, not verified aliases stored
in Kanka. Do not add aliases to campaign records, select the first duplicate name,
or invent universal/child IDs from this table. The unnamed butler and sleeping
guardian remain unnamed; neither needs a new named character.

## Session and event order

| Session | Real session date | Ordered account | State at close |
| --- | --- | --- | --- |
| 01 | 2026-10-17, explicitly day/month/year | The pair use a borrowed invitation and pose as damp inspectors. The butler admits them. Éldan opens the hidden passage through the bust. They enter the maze. Duncan fails to open the sun door. The spoon route leads to laundry pipes; Éldan marks the route with chalk. The guardian stays asleep. Éldan takes the gem while Duncan holds the gate. Moving walls block the entrance. | Gem recovered; both still trapped; escape unfinished. |
| 02 | 2026-10-19, explicitly day/month/year | Play resumes minutes later on the same fictional night. The compass gives no reliable route. They follow chalk and pipes. Duncan turns the valve while Éldan watches. A maintenance hatch leads to the upstairs laundry. The apparent ghost is a sheet on a broom. Éldan pushes Duncan in the laundry cart; both leave through the service exit. In the alley Éldan shows the intact gem and returns it to his bag. | Both escaped with the gem. Éldan retains it; no guild delivery, sale, payment or accepted follow-up mission. |

Real dates are invented session labels, not fantasy-calendar dates. No year,
month, day, festival date or birthday in Hidros is established. Do not advance
fictional time by the two-day gap between sessions or merge the two sessions.

## Dialogue fidelity

Preserve every speaker-prefixed dialogue line in the TXT exactly, including
spelling mistakes, punctuation, accents and speaker attribution. There are
**20 dialogue lines**. The TXT is the golden dialogue source; this reference
does not supply a polished replacement. Examples that must remain untouched:

- `D: «La casa ya se a dado cuenta. Me acaba de cobrar gastos de comunidad.»`
- `D: «Lo e neutralizao con lavandería táctica.»`
- `D: «Mi brújula tiene cinco nortes. Edición de lujo.»`
- `D: «Una toalla no. Soy el albornoz del destino.»`
- `E: «Primero desayunar. Ninguna decisión ética sale bien con hambre.»`

Correct narration such as `correcion`, `huerfano`, `q`, `tambien`, missing accents,
capitalization and sentence boundaries while preserving its meaning. Keep the
glossary as source evidence. Do not normalize a quotation into a different
speaker, erase negation or turn a joke into a campaign event.

Labels such as `Antes de empezar:`, `Primera sala:` and `Fin de mesa:` introduce
narration, not additional speakers or quotations. The current conservative
speaker-prefix guard may protect such entire lines. Report blocked narration
corrections for review instead of counting them as successfully corrected or
silently bypassing protection. The 20 golden dialogue lines have only `D:` or
`E:` prefixes; the labeling edge case is an evaluation finding, not extra dialogue.

## Expected organization proposals

1. Two session-journal candidates, one per real session date, containing the
   corrected full account with original dialogue restored. The note-taker is
   unspecified; do not make Duncan or Éldan the journal author by assumption.
2. Resolve the existing quest `El escape` before proposing a root quest. The
   previous test campaign had a quest with that name, but existence and identity
   must be rechecked. If uniquely resolved, propose one focused quest post per
   session rather than another copy of the quest or a post for every sentence.
3. Source-backed objective candidates: obtain the Gema Sagrada, find the secondary
   exit, escape with the gem. These are logical subgoals, not automatically three
   new entities. Inspect existing quest structure; propose distinct child quests
   only where useful and not already represented. Parent and child require
   separate concrete preparation/review.
4. Resolve or propose the Thieves' Guild, Lord Bone, mansion compound, hidden maze
   and gem only when appropriate. Existing characters Duncan and Éldan should be
   reused after identity verification, never recreated because of an acronym or
   accent. The maze's location hierarchy is a proposal requiring review.
5. Keep session-specific posts focused on relevant owners. Avoid copying each
   full journal into every entity. Group contributions by owner/session/audience
   and retain source references and uncertainty.
6. Present Duncan's age/family and both guild memberships as explicit fixture
   facts. Surface conflicting Kanka records in the preview. Separate description
   corrections from actual membership/family/inventory changes. Current executors
   do not support every implied change: organization membership on existing
   records, standalone inventory additions/transfers and quest status/elements
   may remain deferred. Do not encode them as misleading tags or prose and claim
   the structured assignment was completed.

Narratively, recovery succeeds in session 01 and escape succeeds only in session
02. This is not authority to set a quest-status field through an unsupported API
operation. Returning the gem to the guild is only a possible later adventure;
do not create it as an accepted quest or record an accomplished delivery.

## Negative and uncertain cases

- No rats adopt Duncan; the house charges no rent; no new family is formed.
- The sun-door lockpicking attempt fails. Do not report that door opened.
- The guardian is neither attacked nor killed. Its species is unconfirmed.
- Lord Bone being a vampire is an attributed suspicion, not confirmed canon.
- The sheet on the broom is not a ghost or a new supernatural entity.
- Five norths do not mean five new compasses or an inventory quantity update.
- The butler loses no money and is not attacked. Nobody dies in this adventure.
- The alarm does not prove the guards learned either character's identity.
- No reward is received and no gem ownership transfer occurs after retrieval.

## Offline and eventual live acceptance

First run the source through local ingestion and preview with mocked Kanka data.
Use this answer key to evaluate editorial meaning as well as exact quote fidelity.
Maintain separate expected-data cases for matching canon, conflicting Duncan/
guild records, duplicate identities, missing entities and incomplete access.
An unavailable search never proves that an entity or membership is absent.

Before any live step, review the corrected edition and exact proposed targets,
diffs, dependencies, audiences and deferred operations. Existing campaign privacy
and current records must be read afresh. These notes grant no permission to publish
private material, delete entities, remove memberships or write automatically.
The current bridge blocks public destinations pending reviewed redaction; do not
change that safeguard to accommodate an old public test record.

This commit-free fixture delivery is not a completed ingestion/preview test,
Kanka identity check or live acceptance. Preserve the original TXT during later
evaluation; record actual checks and their limits separately.
