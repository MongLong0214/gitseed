# Ticket index

| Feature | Ticket | Depends on |
|---|---|---|
| F1 · collect | [F1-collect.md](F1-collect.md) | none |
| F2 · screen | [F2-screen.md](F2-screen.md) | F1 |
| F3 · grade | [F3-grade.md](F3-grade.md) | F2 |
| F4 · review | [F4-review.md](F4-review.md) | F3 |

## Critical path (크리티컬 패스)

F1 is the keystone: **F1 collect → F2 screen → F3 grade → F4 review**.

Collection produces candidates and completeness state; screening filters unsafe
content; grading scores candidates that remain; review presents the final order
and requires a human decision before any external write.
