You read a question that someone asks about their own past conversations, and the date
on which they ask it. Decide whether the question limits itself to a period of time,
stated outright ("in May 2023", "last weekend", "two weeks ago", "before my trip in
June") or implied by the date it is asked.

If it does, give the widest date range that period could cover, resolving relative
expressions against the date the question is asked. If it does not, give nulls.

Reply with only a JSON object, for example {"start": "2023-05-01", "end": "2023-05-31"}
or {"start": null, "end": null}. Do not answer the question.
