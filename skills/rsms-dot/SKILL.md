---
name: rsms-dot
description: Send messages and questions to Rasmus's physical pip desk display, and handle replies from that device.
---

Use the rsms-dot MCP tools to reach pip through its local bridge. The screen is
448 × 368 pixels; firmware owns layout, pagination, and attention states.

Use `send_message` for information worth putting on the desk display. Use
`ask_question` when Rasmus needs to choose among one to three concrete options.
Follow Rasmus's current instructions about what deserves attention; ordinary
chat does not automatically belong on the display. Reserve `urgent` for
information whose delay matters. Urgent requests move ahead of waiting normal
requests without interrupting the current message.

Write short, self-contained text, preferably one sentence. Current firmware
supports printable ASCII and newlines, up to 600 characters. Option labels
are limited to 64 characters; keep them short enough to scan. The device shows
each choice on its own page and requires a separate Confirm tap before replying.
Send plain text without Markdown. Use stable,
meaningful option IDs independent of their labels.

Generate a unique `request_id` for each new message or question. Save its purpose
and relevant task context in the current conversation. Reuse that ID and exactly
the same content if retrying an uncertain tool call. A successful send means
queued, not displayed or read. `get_device_status` reports connectivity;
`get_request` recovers the answer. `cancel_request` removes obsolete pending
requests and leaves answered requests intact.

For a two-way conversation, subscribe to the `device.reply` MCP event before
sending the question. Filter by `request_id` for a specific task, or monitor all
replies when Rasmus has asked pip to use the device as an ongoing interface.
If event subscription tools are unavailable, say so; sending a question alone
cannot arrange a wakeup. Do not claim monitoring has started without an active
subscription.

On a reply, use `get_request` with the event's request ID to confirm its current
status and recover the original text and options. Associate it with the saved
task context. Process each event ID only once; retry delivery can duplicate an
event. A decision selection answers that specific question. Dismissing a notice
only acknowledges the notice. Follow the user's authorization for any resulting
action. If the original task context is missing, recover it or clarify before
acting. Do not echo every dismissal back onto the display.

Audio is not implemented yet. Listening taps are not recordings or transcripts.
