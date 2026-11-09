# Alarms

For everyone. Admins also set who is told.

## Levels

| Level | Shape | Meaning | Who is told (usual setting) |
|---|---|---|---|
| **Info** | ● | Something to note, for example a filter needs changing. | Shown on screen only. |
| **Warning** | ▲ | Needs attention soon, for example a temperature out of range. | Email and phone notification (ntfy). |
| **Critical** | ◆ | Needs attention now, for example a plant failure, or the touch screen taking over from the server. | Email, phone notification and SMS. |

The level is always shown by its name and shape as well as by colour. Critical alarms cannot be muted.

## What raises an alarm

- A value above or below a limit, or in a given state (for example a fault input on), for longer than its delay.
- A value that has stopped updating (stale).
- A device that stops answering, where the installer set an offline alarm.
- A **service due**: equipment has run the hours, or made the starts, set as its service interval. An admin marks the
  service done on Setup → Equipment, which clears it.

A value that is Bad (the device not answering) never raises a limit alarm. You get one offline alarm instead of a flood.

## Seeing and acknowledging alarms

- The **Alarms** button at the top shows how many alarms are waiting, with the worst level's shape.
- **Active alarms** lists them. Operators and admins **Acknowledge** an alarm, with an optional comment, to say someone
  is dealing with it. Comments can be added at any time.
- An alarm stays in the list until it is acknowledged **and** back to normal. If it comes back before it was
  acknowledged, it is raised again without a second notification.
- **Alarm history** lists every alarm with when it was raised, acknowledged and back to normal, and who did what. Pick an
  alarm to see its notifications: who was sent what, and whether it arrived.
- The touch screen's Overview lists active alarms (view only).

## Who is told (admins)

**Setup → Alarm delivery:**

- **Recipients:** each person's name, email, mobile number and ntfy topic, and which levels they receive.
  - Make sure someone receives Critical; the page warns if nobody does.
  - **Quiet hours** hold Warnings overnight and send them as one summary when the quiet hours end. Critical is always sent
    at once.
- **Channels per level:** which of email, ntfy and SMS each level uses.
- **Test:** sends a test message to a person and shows the result.

**ntfy** is a phone app for notifications. Each person installs it and subscribes to their own long, random topic name
(like a password), which is entered as their ntfy topic here.

The email account and the SMS service are set up by your installer; their passwords are kept on the server, not in the
web app.

If the internet is down, notifications are retried for up to 24 hours. The alarms themselves are always on screen.
