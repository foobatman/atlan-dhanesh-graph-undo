Build the undo you designed, scoped down to a single process. Spend no more than two hours. We care more about the semantics being right and checkable than about breadth.
What is fixed
 You do not need to choose any of the following.
Language. Python, TypeScript, Go, Rust or Java.
Storage. One SQLite file. No server, no ORM required, no other datastore.
Interface. A command-line tool with the four commands below. No HTTP, no UI, no auth.
Data model. Nodes have id (UUID), qualified_name (unique among live nodes), type, and three authored fields: description, owner, tags (a list). Edges have from_id, to_id and type, where type is contains or references. One derived field: effective_tags, the union of a node's own tags and the tags of every ancestor along contains edges.
Cascade. Deleting a subtree removes the root, every descendant along contains edges, and every edge touching any of them in either direction. effective_tags of the remaining nodes is recomputed.
Actors. Every operation carries an actor of kind human or ingest. Ingest upserts match on qualified_name and mint a new id when no live node has that name; they never set description, owner or tags.
Fixture. You generate it with a script in your repository, deterministic from a seed value: seed.jsonl (about 5,000 nodes and edges) and ops.jsonl (one delete-subtree of at least 500 nodes, then about 300 operations of mixed kinds). The operations must include human edits to neighbours, edges re-pointed to other nodes, an ingest run that recreates at least 50 of the deleted qualified names under new ids, and human edits to at least 5 of those recreated nodes.
Commands
load seed.jsonl. Create the database from the seed.
apply ops.jsonl. Apply operations in order. Each has an op_id and an actor. Record enough that any delete-subtree can be undone later.
undo <op_id>. Apply the inverse of that operation to the current state using the semantics you designed. Print a report as JSON: for every node and edge the original operation removed, one of restored, merged or skipped, with a reason for merged and skipped.
dump. Print the live graph as sorted JSONL, for comparison.

Invariants your checker must enforce
You write the checker; it must test these against the dump and the report, and it must not share code with undo. They hold whichever conflict rules you chose. DECISIONS.md states the rules.
No change made by a human actor after the delete is lost or altered by the undo.
Every node the delete removed whose qualified_name is not live at undo time, and whose parent is live, is restored with its authored fields intact.
After undo, no two live nodes share a qualified_name.
The report accounts for every removed node and edge exactly once.
effective_tags is correct for every live node after undo.
undo is idempotent: running it twice gives the same dump.
undo is resumable: with the environment variable CRASH_AFTER=<n> set, the tool exits after n writes to the database; running undo again without it reaches the same final dump as an uninterrupted run. A dump taken between the two runs must contain no edge to a node that does not exist.
The whole scenario completes in under 60 seconds on a laptop.
Out of scope
Redo, retention, authorisation, side effects outside the database. If you have time left, redo is the one extension we would read.
Deliverables
A repository you create. README with the exact commands to generate the fixture, run the scenario and run the checker. DECISIONS.md (one page): the conflict rule you chose for each of independent, conflicting and caused edits; what the model proposed that you rejected and why; what you verified yourself. All AI sessions, unedited, as described in the first tab.
