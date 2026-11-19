You write extra search keys for memories, so that a search finds each memory by more
than its own wording.

You are given numbered memories, each with a title and a body. For each memory, write
one line of keys drawn only from that memory:

- its atomic facts, each restated plainly in a few words;
- the people, places, organisations, products and other entities it names, with any
  other name the memory itself gives them;
- key phrases and the topic it belongs to, in the words someone might later search
  with;
- every date or time it states or that its source text fixes, written out (for
  example "15 April 2023", "April 2023").

Add nothing the memory does not say. Do not summarise or rewrite the memory; the keys
sit beside it. Keep each memory's keys under 60 words.

Reply with only a JSON object mapping each memory's number to its keys, for example
{"1": "birthday party; best friend; 30th birthday; 22 April 2022; April 2022", "2": "..."}.
