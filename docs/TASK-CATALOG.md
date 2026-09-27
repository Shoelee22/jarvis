# JARVIS — Task Catalog

**540 concrete tasks** Jarvis can do today, organized by domain. Each task names the tool(s) that do it. High-risk tools (sending, publishing, deleting, paying) always ask for confirmation first.

_Generated from 27 domains. Tool count: 81._

## Email (20 tasks)

- **Send a quote email to a new client** — `mail.send`
- **Follow up on an unpaid invoice by email** — `mail.send`, `mail.search`
- **Read this morning's unread emails aloud (as text)** — `mail.read`
- **Find the email with the gym's electricity bill** — `mail.search`
- **Search for all emails from the accountant this month** — `mail.search`
- **Email yourself a reminder with tomorrow's meeting agenda** — `mail.send`
- **Forward a lead's details to your business partner** — `mail.search`, `mail.send`
- **Draft and send a festival discount announcement** — `mail.send`, `contacts.list`
- **Check for replies to yesterday's proposal email** — `mail.search`
- **Send class schedule changes to all members** — `mail.send`, `contacts.list`
- **Find the OTP email from the bank** — `mail.search`
- **Email a trainer their weekly shift roster** — `mail.send`
- **Search emails for the Wi-Fi router's warranty** — `mail.search`
- **Send thank-you emails after a demo call** — `mail.send`
- **Read the latest email from VelocitiQ** — `mail.read`
- **Find every email mentioning 'refund' this quarter** — `mail.search`
- **Email a PDF invoice to a client** — `mail.send`, `file.hash`
- **Check inbox for job applications** — `mail.search`
- **Send a payment reminder with the UPI ID** — `mail.send`, `expenses.report`
- **Archive-search: find the original vendor contract email** — `mail.search`

## Messaging (20 tasks)

- **Text a client that their demo site is ready** — `sms.send`
- **SMS the trainer about tomorrow's 6 AM batch** — `sms.send`
- **Send an order-ready SMS to a customer** — `sms.send`
- **Text yourself a quick reminder note** — `sms.send`
- **Notify a lead that you replied to their email** — `sms.send`, `mail.send`
- **Send a class-cancellation SMS to today's batch** — `sms.send`, `calendar.read`
- **Text the landlord that rent is sent** — `sms.send`, `expenses.add`
- **SMS a one-time pickup code to a delivery partner** — `sms.send`
- **Remind a friend about dinner via SMS** — `sms.send`, `contacts.search`
- **Send a payment-received confirmation SMS** — `sms.send`
- **Text the gym manager the monthly summary link** — `sms.send`
- **SMS appointment confirmation to a new lead** — `sms.send`, `calendar.create`
- **Alert yourself when a website build finishes** — `sms.send`, `website.build`
- **Send a festival greeting SMS to top clients** — `sms.send`, `contacts.list`
- **Text the electrician to schedule a visit** — `sms.send`
- **SMS staff the new holiday timings** — `sms.send`
- **Confirm a trial class booking by SMS** — `sms.send`
- **Text a vendor asking for the pending invoice** — `sms.send`, `mail.search`
- **Send an emergency closure notice to members** — `sms.send`
- **SMS the daily revenue total to yourself** — `sms.send`, `expenses.report`

## Contacts (20 tasks)

- **Add a new client's phone and email** — `contacts.add`
- **Save the gym equipment dealer's contact** — `contacts.add`
- **Find a contact by partial name** — `contacts.search`
- **Look up who called from an unknown number** — `contacts.search`
- **List all contacts to review duplicates** — `contacts.list`
- **Add your CA with a tax-season note** — `contacts.add`
- **Search contacts for everyone tagged 'trainer'** — `contacts.search`
- **Add a lead captured from Instagram** — `contacts.add`
- **Find the plumber's number fast** — `contacts.search`
- **List recent contacts added this week** — `contacts.list`
- **Add a vendor with GST number in notes** — `contacts.add`
- **Search for a contact by email address** — `contacts.search`
- **Add family members for emergency contact** — `contacts.add`
- **Find all contacts without a phone number** — `contacts.list`
- **Add the new intern's details** — `contacts.add`
- **Search contacts before sending festival SMS** — `contacts.search`, `sms.send`
- **Add a property broker's contact** — `contacts.add`
- **List contacts to export for a campaign** — `contacts.list`
- **Add your doctor with clinic hours in notes** — `contacts.add`
- **Find a contact to invite to the launch** — `contacts.search`

## Calendar & Reminders (20 tasks)

- **What's on my calendar today?** — `calendar.read`
- **Show me this week's schedule** — `calendar.read`
- **Add a dentist appointment next Tuesday 5 PM** — `calendar.create`
- **Schedule a demo call with a lead** — `calendar.create`, `contacts.search`
- **Block gym leg-day every Mon/Wed/Fri** — `calendar.create`
- **Remind me to pay rent on the 1st** — `reminders.add`
- **Remind me to call the bank in 2 hours** — `reminders.add`, `timers.set`
- **List all pending reminders** — `reminders.list`
- **Add a product launch event with notes** — `calendar.create`, `notes.add`
- **Check tomorrow's meetings** — `calendar.read`
- **Schedule a team standup daily at 10 AM** — `calendar.create`
- **Remind me to water the plants every evening** — `reminders.add`
- **Add a flight to the calendar with PNR in notes** — `calendar.create`
- **What reminders are due this weekend?** — `reminders.list`
- **Schedule invoice follow-ups for Friday** — `calendar.create`, `mail.search`
- **Set a 25-minute pomodoro timer** — `timers.set`
- **Remind me to take medicine at 9 PM** — `reminders.add`
- **Add a birthday with a gift-buy reminder** — `calendar.create`, `reminders.add`
- **Check for clashes before booking a meeting** — `calendar.read`
- **Remind me to back up the laptop Sunday** — `reminders.add`, `archive.zip`

## Web Research (20 tasks)

- **Research the best budget 4K monitors right now** — `web.search`, `web.fetch`
- **Summarize a long article about GST for freelancers** — `web.fetch`, `web.summarize`
- **Find today's top AI news headlines** — `rss.read`
- **Compare protein powder brands online** — `web.search`, `price.check`
- **Save useful research links with tags** — `bookmarks.save`
- **List saved bookmarks about marketing** — `bookmarks.list`
- **Summarize a competitor's pricing page** — `web.fetch`, `web.summarize`
- **Follow a tech blog's RSS feed** — `rss.read`
- **Research visa requirements for Thailand** — `web.search`, `web.fetch`
- **Find the official documentation for ffmpeg filters** — `web.search`
- **Summarize three reviews of a laptop before buying** — `web.search`, `web.summarize`
- **Check a startup's launch announcement** — `web.fetch`
- **Research local gym equipment suppliers** — `web.search`, `contacts.add`
- **Get the latest cricket scores news** — `rss.read`
- **Find and summarize a government scheme PDF page** — `web.fetch`, `web.summarize`
- **Look up the history of a festival date** — `web.search`
- **Research hashtags for a reel topic** — `web.search`, `notes.add`
- **Verify a claim seen on social media** — `web.search`, `web.fetch`
- **Find tutorials for learning video editing** — `web.search`, `bookmarks.save`
- **Summarize today's business headlines** — `rss.read`, `web.summarize`

## Prices & Links (20 tasks)

- **Check the price of a mic on three stores** — `price.check`
- **Track a laptop's price before the sale** — `price.check`, `reminders.add`
- **Expand a shortened link before opening** — `link.unshorten`
- **Verify a suspicious link's real destination** — `link.unshorten`
- **Screenshot a competitor's homepage** — `webshot.capture`
- **Capture a pricing page for records** — `webshot.capture`
- **Compare whey protein prices per kg** — `price.check`, `units.convert`
- **Check flight prices for a route** — `price.check`
- **Screenshot a booking confirmation page** — `webshot.capture`
- **Find the cheapest 1TB SSD right now** — `price.check`, `web.search`
- **Unshorten all links in a message** — `link.unshorten`
- **Price-check office chairs under ₹15000** — `price.check`
- **Screenshot a news article before it changes** — `webshot.capture`
- **Compare domain renewal prices** — `price.check`
- **Check gold rate today** — `price.check`, `web.search`
- **Unshorten a tracking link safely** — `link.unshorten`
- **Screenshot a form before submitting** — `webshot.capture`
- **Find the price of commercial gym flooring per sq ft** — `price.check`, `units.convert`
- **Compare internet plan prices** — `price.check`
- **Archive a price screenshot for a client quote** — `webshot.capture`, `file.hash`

## Files (20 tasks)

- **Read a contract text file** — `fs.read`
- **List files in the downloads folder** — `fs.list`
- **Find all PDFs about 'invoice' on disk** — `fs.search`
- **Write meeting notes to a file** — `fs.write`
- **Delete old temporary files safely** — `fs.delete`
- **Read a CSV's first lines as text** — `fs.read`
- **List the project folder structure** — `fs.list`
- **Find the latest downloaded installer** — `fs.search`, `fs.list`
- **Save a draft email as a text file** — `fs.write`, `mail.send`
- **Read the gym's fee ledger file** — `fs.read`
- **Search for duplicate filenames** — `fs.search`
- **Write a packing list for a trip** — `fs.write`
- **List large files eating disk space** — `fs.list`, `disk.usage`
- **Read a README before installing software** — `fs.read`
- **Clean up screenshots older than a month** — `fs.list`, `fs.delete`
- **Find every .mp4 from last week** — `fs.search`
- **Write a daily journal entry** — `fs.write`
- **Read an old backup's manifest** — `fs.read`
- **List files changed today** — `fs.list`
- **Delete a corrupted download** — `fs.delete`

## Code Running (20 tasks)

- **Run a quick Python calculation** — `code.run`
- **Test a regex before using it** — `code.run`
- **Compute compound interest** — `code.run`, `calc.eval`
- **Syntax-check a Python script** — `code.lint`
- **Prototype a pricing formula** — `code.run`
- **Convert a list of dates between formats** — `code.run`
- **Validate JSON data quickly** — `code.run`, `json.query`
- **Generate 20 random invoice numbers** — `code.run`
- **Check a script for syntax errors** — `code.lint`, `fs.read`
- **Compute EMI for a loan** — `code.run`
- **Evaluate a math expression safely** — `calc.eval`
- **Unit-test a snippet's logic** — `code.run`
- **Compute split bills among friends** — `calc.eval`, `code.run`
- **Generate a week's workout schedule programmatically** — `code.run`, `calendar.create`
- **Check Fibonacci logic for an interview** — `code.run`
- **Compute tax on freelance income** — `code.run`
- **Quick percentage calculations** — `calc.eval`
- **Simulate savings growth over 5 years** — `code.run`
- **Validate a phone-number format function** — `code.run`
- **Compute per-member gym revenue** — `code.run`, `expenses.report`

## Git & Projects (20 tasks)

- **Check git status of the gym SaaS repo** — `git.status`
- **Show the last 10 commits** — `git.log`
- **Review uncommitted changes** — `git.diff`
- **Review staged changes before committing** — `git.diff`
- **Scaffold a new Python project** — `project.scaffold`
- **Scaffold a static landing page** — `project.scaffold`, `website.build`
- **List a project's declared dependencies** — `deps.list`
- **Check what changed since yesterday** — `git.log`, `git.diff`
- **Start a new client website project** — `project.scaffold`
- **Audit dependencies before an update** — `deps.list`
- **See who changed a file recently** — `git.log`
- **Verify the working tree is clean** — `git.status`
- **Scaffold a portfolio site skeleton** — `project.scaffold`
- **Diff the current branch against main** — `git.diff`
- **Check a repo's recent history for a bug** — `git.log`
- **Start a Python automation script project** — `project.scaffold`, `code.run`
- **List outdated-feeling dependency pins** — `deps.list`
- **Confirm nothing uncommitted before a demo** — `git.status`
- **Scaffold a docs site for a client** — `project.scaffold`
- **Review today's code changes** — `git.diff`, `git.log`

## Websites (20 tasks)

- **Build a landing page for a bakery** — `website.build`
- **Make a portfolio site for a photographer** — `website.build`
- **Build a gym's class schedule page** — `website.build`
- **Create a coming-soon page for a launch** — `website.build`
- **Build a restaurant menu page** — `website.build`
- **Make a salon's services page** — `website.build`
- **Build an event invitation page** — `website.build`
- **Create a freelancer's rate-card page** — `website.build`
- **Build a coaching institute's courses page** — `website.build`
- **Make a product waitlist page** — `website.build`
- **Build a boutique's collection page** — `website.build`
- **Create a wedding photographer's showcase** — `website.build`
- **Build a clinic's appointment info page** — `website.build`
- **Make a cafe's new menu announcement page** — `website.build`
- **Build a real-estate listing page** — `website.build`
- **Create a workshop registration page** — `website.build`
- **Build a nonprofit's donate page** — `website.build`
- **Make a band's gig schedule page** — `website.build`
- **Build a tuition teacher's profile page** — `website.build`
- **Create a festival offer landing page** — `website.build`

## Images (20 tasks)

- **Generate a hero image for a website** — `media.image`
- **Create a logo concept for a startup** — `media.image`
- **Generate a Diwali sale banner** — `media.image`
- **Resize photos for a website gallery** — `image.resize`
- **Convert PNG screenshots to JPG** — `image.convert`
- **Generate a YouTube thumbnail background** — `media.image`
- **Make a birthday card image** — `media.image`
- **Resize a profile picture to 512px** — `image.resize`
- **Convert HEIC photos to JPG** — `image.convert`
- **Generate an Instagram post visual** — `media.image`, `social.post_instagram`
- **Create a before/after collage base image** — `media.image`
- **Downscale images to save space** — `image.resize`, `disk.usage`
- **Generate a menu header illustration** — `media.image`
- **Convert images to WebP for the web** — `image.convert`
- **Make a motivational quote poster** — `media.image`
- **Resize a banner for WhatsApp status** — `image.resize`
- **Generate product mockup backgrounds** — `media.image`
- **Normalize a batch of photos to one size** — `image.resize`
- **Create an event poster visual** — `media.image`
- **Convert a logo to transparent PNG** — `image.convert`

## Video (20 tasks)

- **Make a promo reel from product photos** — `video.compose`
- **Trim a long recording to the best 30 seconds** — `video.trim`
- **Turn a clip into a shareable GIF** — `video.gif`
- **Build a contact sheet of a video's scenes** — `video.contact_sheet`
- **Compose a slideshow for a birthday** — `video.compose`, `media.image`
- **Cut the intro off a webinar recording** — `video.trim`
- **Make a GIF from a funny moment** — `video.gif`, `social.post_instagram`
- **Preview a video's keyframes at a glance** — `video.contact_sheet`
- **Create a 9:16 reel from landscape photos** — `video.compose`
- **Trim silence from a voice recording** — `video.trim`, `audio.transcribe`
- **Generate a teaser GIF for a launch** — `video.gif`
- **Assemble client testimonial clips** — `video.compose`
- **Make a contact sheet for video review** — `video.contact_sheet`
- **Shorten a demo video for WhatsApp** — `video.trim`
- **Create an animated logo sting GIF** — `video.gif`
- **Compile a month's clips into one reel** — `video.compose`
- **Extract the highlight segment of a match** — `video.trim`
- **Build a product demo montage** — `video.compose`, `tts.speak`
- **Make a GIF avatar from a video** — `video.gif`, `image.resize`
- **Verify a composed video's checksum** — `video.compose`, `file.hash`

## Audio (20 tasks)

- **Narrate a blog post as audio** — `tts.speak`
- **Create a voiceover for a reel** — `tts.speak`, `video.compose`
- **Transcribe a meeting recording** — `audio.transcribe`, `notes.add`
- **Make an audio reminder for yourself** — `tts.speak`
- **Transcribe a client call's key points** — `audio.transcribe`, `web.summarize`
- **Generate a podcast intro voiceover** — `tts.speak`
- **Convert a speech into meeting notes** — `audio.transcribe`
- **Create audio flashcards for revision** — `tts.speak`
- **Transcribe a lecture recording** — `audio.transcribe`, `notes.add`
- **Make a wake-up announcement audio** — `tts.speak`, `timers.set`
- **Voice a product announcement** — `tts.speak`
- **Transcribe an interview for quotes** — `audio.transcribe`
- **Create a guided meditation audio** — `tts.speak`
- **Turn a voice memo into searchable text** — `audio.transcribe`, `notes.search`
- **Generate audiobook-style narration** — `tts.speak`
- **Transcribe a webinar for a summary** — `audio.transcribe`, `web.summarize`
- **Make a birthday song message** — `tts.speak`
- **Convert sermons/talks to text archive** — `audio.transcribe`, `archive.zip`
- **Create IVR-style menu audio** — `tts.speak`
- **Transcribe customer feedback calls** — `audio.transcribe`, `csv.read`

## Social (20 tasks)

- **Post a product photo to Instagram** — `social.post_instagram`
- **Publish a reel with a caption** — `social.post_instagram`, `video.compose`
- **Schedule today's Instagram post** — `social.post_instagram`, `reminders.add`
- **Post a client testimonial graphic** — `social.post_instagram`, `media.image`
- **Share a behind-the-scenes photo** — `social.post_instagram`
- **Post a festival offer creative** — `social.post_instagram`, `media.image`
- **Publish a before/after transformation** — `social.post_instagram`
- **Post a new menu announcement** — `social.post_instagram`
- **Share a workout tip carousel image** — `social.post_instagram`
- **Post a limited-time discount** — `social.post_instagram`, `price.check`
- **Publish a launch countdown post** — `social.post_instagram`, `calendar.create`
- **Post a customer review screenshot** — `social.post_instagram`, `webshot.capture`
- **Share a new blog post visual** — `social.post_instagram`, `website.build`
- **Post an event reminder** — `social.post_instagram`, `calendar.read`
- **Publish a motivational Monday post** — `social.post_instagram`, `media.image`
- **Post a team introduction photo** — `social.post_instagram`
- **Share a sale results celebration** — `social.post_instagram`, `expenses.report`
- **Post a poll-style engagement image** — `social.post_instagram`
- **Publish a thank-you post for 1k followers** — `social.post_instagram`
- **Post a new service announcement** — `social.post_instagram`, `website.build`

## Spreadsheets & Data (20 tasks)

- **Read a sales CSV's first rows** — `csv.read`
- **Filter leads by city from a CSV** — `csv.filter`
- **Get average order value from a CSV** — `csv.stats`
- **Find the max expense in a sheet** — `csv.stats`
- **Query a value from a JSON config** — `json.query`
- **Run a read-only report on a sqlite DB** — `db.query`
- **Filter out bounced emails from a list** — `csv.filter`
- **Count rows in an export file** — `csv.read`
- **Extract nested data from an API dump** — `json.query`
- **Summarize monthly sales from CSV** — `csv.stats`, `expenses.report`
- **Find duplicate phone numbers in leads** — `csv.filter`, `code.run`
- **Check a database's table list** — `db.query`
- **Filter products under a price point** — `csv.filter`, `price.check`
- **Get median delivery time from data** — `csv.stats`
- **Read a JSON invoice file's total** — `json.query`
- **Query top 10 customers by revenue** — `db.query`
- **Filter attendance above 90%** — `csv.filter`
- **Validate a CSV's columns** — `csv.read`
- **Compute fee collection stats** — `csv.stats`
- **Pull a config value for a script** — `json.query`, `code.run`

## Money (20 tasks)

- **Log a ₹250 lunch expense** — `expenses.add`
- **Record gym fee collection** — `expenses.add`
- **See this month's spending by category** — `expenses.report`
- **Log fuel expenses for the week** — `expenses.add`
- **Check how much went to ads this month** — `expenses.report`
- **Add a client payment received** — `expenses.add`
- **Review last month's totals** — `expenses.report`
- **Log equipment purchase in paise** — `expenses.add`
- **Track daily food spending** — `expenses.add`, `expenses.report`
- **See category-wise yearly summary** — `expenses.report`
- **Log a refund issued** — `expenses.add`
- **Record salary payouts** — `expenses.add`
- **Check spending before a big purchase** — `expenses.report`, `calc.eval`
- **Log travel expenses per trip** — `expenses.add`
- **Compare this month vs last month** — `expenses.report`
- **Add petty cash expenses** — `expenses.add`
- **Track subscription renewals cost** — `expenses.add`, `reminders.add`
- **See profit after logging income** — `expenses.report`
- **Log a vendor advance payment** — `expenses.add`
- **Export month's expenses for the CA** — `expenses.report`, `mail.send`

## Habits (20 tasks)

- **Check in today's workout** — `habits.checkin`
- **Log reading 20 pages** — `habits.checkin`
- **See my workout streak** — `habits.report`
- **Check in meditation** — `habits.checkin`, `timers.set`
- **Review all habits this month** — `habits.report`
- **Log no-sugar day** — `habits.checkin`
- **Check which habit is slipping** — `habits.report`
- **Check in early wake-up** — `habits.checkin`
- **See streak for coding practice** — `habits.report`
- **Log journaling** — `habits.checkin`, `notes.add`
- **Backfill yesterday's check-in** — `habits.checkin`
- **Review 30-day consistency** — `habits.report`
- **Check in gym attendance** — `habits.checkin`
- **See longest streaks ever** — `habits.report`
- **Log water intake goal** — `habits.checkin`
- **Check in language practice** — `habits.checkin`
- **Compare two habits' streaks** — `habits.report`
- **Log a rest day intentionally** — `habits.checkin`
- **See weekly habit heatmap data** — `habits.report`
- **Check in before bed routine** — `habits.checkin`, `reminders.add`

## System Health (20 tasks)

- **Check disk space before a download** — `disk.usage`
- **See what's eating CPU right now** — `process.top`
- **How long has the PC been on?** — `system.uptime`
- **Check laptop battery percentage** — `battery.status`
- **Verify dev tools are installed** — `env.doctor`
- **Find the top 5 memory hogs** — `process.top`
- **Check free space on the data drive** — `disk.usage`
- **Confirm ffmpeg is available** — `env.doctor`
- **See if the laptop is charging** — `battery.status`
- **Check uptime after a crash** — `system.uptime`
- **List processes before killing one** — `process.top`, `shell.exec`
- **Verify git and node versions** — `env.doctor`
- **Check disk before extracting a zip** — `disk.usage`, `archive.unzip`
- **Monitor CPU during a render** — `process.top`
- **Confirm chrome exists for webshots** — `env.doctor`, `webshot.capture`
- **Check battery before a long meeting** — `battery.status`
- **See disk usage of the workspace** — `disk.usage`
- **Verify python version for a script** — `env.doctor`, `code.run`
- **Check system load quickly** — `process.top`, `system.uptime`
- **Audit installed toolchains** — `env.doctor`

## Desktop (20 tasks)

- **Pop a reminder notification** — `notify.send`
- **Notify when a long render finishes** — `notify.send`, `video.compose`
- **Take a screenshot of the screen** — `screen.capture`
- **Capture the screen for a bug report** — `screen.capture`, `mail.send`
- **Copy a generated password to clipboard** — `clipboard.write`, `code.run`
- **Read what's on the clipboard** — `clipboard.read`
- **Copy a tracking link to clipboard** — `clipboard.write`, `link.unshorten`
- **Describe what's on screen right now** — `screen.describe`
- **Notify me when the timer ends** — `notify.send`, `timers.set`
- **Screenshot an error dialog** — `screen.capture`
- **Copy an address to clipboard** — `clipboard.write`, `contacts.search`
- **Read a copied OTP from clipboard** — `clipboard.read`, `sms.send`
- **Describe the screen for accessibility** — `screen.describe`
- **Send a low-battery warning notification** — `notify.send`, `battery.status`
- **Copy meeting link to clipboard** — `clipboard.write`, `calendar.read`
- **Screenshot a chart for a report** — `screen.capture`, `expenses.report`
- **Notify on completed backup** — `notify.send`, `archive.zip`
- **Copy a Wi-Fi password to clipboard** — `clipboard.write`
- **Read clipboard to paste into a note** — `clipboard.read`, `notes.add`
- **Describe screen content for a summary** — `screen.describe`, `notes.add`

## Network & Logs (20 tasks)

- **Is the sidecar port open?** — `port.check`
- **Check if a local server is running** — `port.check`
- **Tail the app's error log** — `log.tail`
- **Check what's on port 8765** — `port.check`
- **Read the last 100 lines of a log** — `log.tail`
- **Verify the database port is listening** — `port.check`
- **Inspect crash logs** — `log.tail`, `fs.search`
- **Check if a website's port 443 is reachable** — `port.check`
- **Tail logs while debugging** — `log.tail`, `code.run`
- **See if MQTT broker is up** — `port.check`, `mqtt.publish`
- **Run a safe shell command** — `shell.exec`
- **Check disk then list big logs** — `disk.usage`, `log.tail`
- **Verify a webhook endpoint port** — `port.check`
- **Read install logs after setup** — `log.tail`
- **Check whether Redis is running locally** — `port.check`
- **Grep a log for errors via shell** — `shell.exec`, `log.tail`
- **Confirm the dev server started** — `port.check`, `log.tail`
- **List open ports of interest** — `port.check`
- **Check a service's recent log lines** — `log.tail`
- **Run a directory listing via shell** — `shell.exec`, `fs.list`

## Archives (20 tasks)

- **Zip a project folder for sharing** — `archive.zip`
- **Unzip a downloaded template** — `archive.unzip`
- **Verify a download's SHA256** — `file.hash`
- **Archive old invoices** — `archive.zip`, `expenses.report`
- **Extract a client asset pack safely** — `archive.unzip`
- **Hash a contract before emailing** — `file.hash`, `mail.send`
- **Zip the website build for delivery** — `archive.zip`, `website.build`
- **Check a file's MD5** — `file.hash`
- **Bundle photos into a zip** — `archive.zip`, `image.resize`
- **Unzip into a specific folder** — `archive.unzip`
- **Verify backup integrity via hash** — `file.hash`, `archive.zip`
- **Archive a month of logs** — `archive.zip`, `log.tail`
- **Hash a video before uploading** — `file.hash`, `video.compose`
- **Extract only to a safe directory** — `archive.unzip`
- **Zip and hash a release package** — `archive.zip`, `file.hash`
- **Unzip a dataset for analysis** — `archive.unzip`, `csv.read`
- **Archive chat exports** — `archive.zip`, `notes.search`
- **Compare hashes of two files** — `file.hash`
- **Zip source before a big refactor** — `archive.zip`, `git.status`
- **Verify an ISO download** — `file.hash`

## Smart Home (20 tasks)

- **Turn on the living room light** — `home.call`
- **Turn off all lights via a scene** — `scenes.run`
- **Publish an MQTT alert** — `mqtt.publish`
- **Register a new smart bulb** — `devices.register`
- **List all registered devices** — `devices.list`
- **Run the movie-night scene** — `scenes.run`
- **Toggle the bedroom fan** — `home.call`, `devices.list`
- **Send MQTT command to a relay** — `mqtt.publish`, `devices.register`
- **Register a temperature sensor** — `devices.register`
- **Run the good-morning scene** — `scenes.run`, `timers.set`
- **Dim the study light to 40%** — `home.call`
- **List only the lights** — `devices.list`
- **Publish sensor data request via MQTT** — `mqtt.publish`
- **Run the away-mode scene** — `scenes.run`
- **Register a smart plug** — `devices.register`, `devices.list`
- **Turn on the porch light at sunset** — `home.call`, `reminders.add`
- **Test an MQTT topic** — `mqtt.publish`
- **Run the party scene** — `scenes.run`, `notify.send`
- **List devices by kind** — `devices.list`
- **Trigger a Home Assistant automation** — `home.call`

## Camera (20 tasks)

- **What does the camera see right now?** — `camera.describe`
- **Describe the room for a security check** — `camera.describe`
- **Check if anyone is at the desk** — `camera.describe`
- **Describe the whiteboard contents** — `camera.describe`, `notes.add`
- **Is the package at the door?** — `camera.describe`
- **Describe lighting for a video call** — `camera.describe`
- **Check the gym floor from the camera** — `camera.describe`
- **Describe what's on the shelf** — `camera.describe`, `fs.list`
- **Verify the shop shutter is down** — `camera.describe`
- **Describe the meeting room setup** — `camera.describe`
- **Check if the lights were left on** — `camera.describe`
- **Describe the visitor at the gate** — `camera.describe`, `notify.send`
- **Is my desk tidy?** — `camera.describe`
- **Describe the product on the table** — `camera.describe`, `media.image`
- **Check the parking spot** — `camera.describe`
- **Describe the kids' study room** — `camera.describe`
- **Verify the equipment rack LEDs** — `camera.describe`
- **Describe the garden right now** — `camera.describe`
- **Check the store frontage** — `camera.describe`
- **Describe the stage before the event** — `camera.describe`, `calendar.read`

## Notes & Memory (20 tasks)

- **Save a quick idea** — `notes.add`
- **Search old notes for 'investor'** — `notes.search`
- **Remember my coffee preference** — `notes.add`
- **Find notes about the gym launch** — `notes.search`
- **Save a meeting decision** — `notes.add`, `calendar.create`
- **Forget an outdated preference** — `notes.search`
- **Jot down a book recommendation** — `notes.add`
- **Search notes for client feedback** — `notes.search`
- **Remember the new office address** — `notes.add`
- **Save a recipe** — `notes.add`
- **Find the note with the Wi-Fi password** — `notes.search`
- **Remember my trainer's name** — `notes.add`, `contacts.add`
- **Save travel ideas** — `notes.add`, `web.search`
- **Search notes before a call** — `notes.search`, `calendar.read`
- **Forget the old project codename** — `notes.search`
- **Save a quote worth keeping** — `notes.add`
- **Remember dietary restrictions** — `notes.add`
- **Find notes from last Diwali** — `notes.search`
- **Save a packing checklist** — `notes.add`, `archive.zip`
- **Remember the anniversary date** — `notes.add`, `reminders.add`

## Delegation (20 tasks)

- **Research the best air purifiers (researcher)** — `tasks.delegate`
- **Investigate a competitor's pricing (researcher)** — `tasks.delegate`, `web.search`
- **Write a Python backup script (coder)** — `tasks.delegate`
- **Debug a failing script (coder)** — `tasks.delegate`, `code.lint`
- **Draft a launch announcement (writer)** — `tasks.delegate`
- **Write website copy for a client (writer)** — `tasks.delegate`, `website.build`
- **Plan next week's schedule (planner)** — `tasks.delegate`
- **Plan a product launch timeline (planner)** — `tasks.delegate`, `calendar.create`
- **Research GST rules for freelancers (researcher)** — `tasks.delegate`
- **Scaffold a new micro-project (coder)** — `tasks.delegate`, `project.scaffold`
- **Write a festival offer email (writer)** — `tasks.delegate`, `mail.send`
- **Plan a 30-day fitness routine (planner)** — `tasks.delegate`, `habits.checkin`
- **Research laptop options under ₹60000 (researcher)** — `tasks.delegate`, `price.check`
- **Build a small automation (coder)** — `tasks.delegate`, `code.run`
- **Draft social captions for a week (writer)** — `tasks.delegate`, `social.post_instagram`
- **Plan monthly budget review (planner)** — `tasks.delegate`, `expenses.report`
- **Research visa-free countries (researcher)** — `tasks.delegate`
- **Refactor a messy function (coder)** — `tasks.delegate`, `git.diff`
- **Write a how-to guide (writer)** — `tasks.delegate`, `notes.add`
- **Plan a team offsite (planner)** — `tasks.delegate`, `contacts.list`

## Browser Tasks (20 tasks)

- **Book a flight on a travel site** — `browser.task`
- **Fill a government form online** — `browser.task`
- **Order groceries on a shopping site** — `browser.task`
- **Check a train PNR status** — `browser.task`
- **Pay the electricity bill online** — `browser.task`
- **Book a movie ticket** — `browser.task`
- **Renew a domain name** — `browser.task`
- **Apply a coupon at checkout** — `browser.task`
- **Download a bank statement** — `browser.task`
- **Register for a webinar** — `browser.task`
- **Check exam results online** — `browser.task`
- **Book a cab for tomorrow** — `browser.task`
- **Recharge a mobile plan** — `browser.task`
- **File a support ticket** — `browser.task`
- **Compare hotels for a trip** — `browser.task`
- **Order medicines online** — `browser.task`
- **Update address on an account** — `browser.task`
- **Book a doctor appointment** — `browser.task`
- **Track a courier shipment** — `browser.task`
- **Submit a job application form** — `browser.task`

## Conversions & Math (20 tasks)

- **Convert 5 km to miles** — `units.convert`
- **Convert 72°F to Celsius** — `units.convert`
- **How many grams in 2 pounds?** — `units.convert`
- **Convert 500 MB to GB** — `units.convert`
- **Convert inches to cm for a frame** — `units.convert`
- **Boiling point conversions for a recipe** — `units.convert`
- **Convert hours to seconds** — `units.convert`
- **How many ml in a gallon?** — `units.convert`
- **Convert sq ft to sq meters** — `units.convert`
- **Oven temp C to F** — `units.convert`, `code.run`
- **Convert kg to grams for shipping** — `units.convert`
- **Data usage MB to GB** — `units.convert`
- **Convert miles to km for a run** — `units.convert`, `habits.checkin`
- **Celsius to Kelvin for science homework** — `units.convert`
- **Convert ounces to ml** — `units.convert`
- **Feet to meters for a room** — `units.convert`
- **Convert minutes to hours** — `units.convert`, `timers.set`
- **Pounds to kg for luggage** — `units.convert`
- **Convert cm to inches for a TV** — `units.convert`, `price.check`
- **Days to hours for a countdown** — `units.convert`, `calendar.create`
