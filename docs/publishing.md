# Publish to YouTube

1. Open **Publish**. In **YouTube connection**, import the JSON for your Google **Desktop app** OAuth client. Enable YouTube Data API v3 in that Google project; if the consent screen is in testing, add your Google account as a test user.
2. Press **Connect YouTube**, then **Continue with Google**. Choose the intended channel. This is separate from the live monitor's API key.
3. Select a finished MP4, edit its title/description, and choose visibility. For a schedule, select Public and a future local time.
4. Save a draft, preview its exact video, then **Approve upload**. The default limit is four uploads per rolling 24 hours.

Drafts do not upload. Each approval applies to that video, metadata and channel. Editing a draft requires a new approval. ClipForge stores a separate video copy, so later editing/deleting a library clip does not change an approved upload.

Keep ClipForge running until the upload finishes. YouTube handles the scheduled publication afterward, even if the PC is off. An uploaded item is not a claim that YouTube has finished processing it; check the linked video or YouTube Studio. Manage or cancel uploaded schedules in Studio.

Network interruptions resume the saved upload session. If YouTube cannot confirm an expired session, the item needs review: check Studio before removing it or creating another draft. Local disconnection does not revoke Google's permissions or delete videos; manage permissions in your Google Account.

OAuth credentials, tokens, upload sessions and queued copies stay under ignored `publishing-data/`. Tokens use Windows DPAPI; on Linux/macOS the credential files use owner-only permissions. No shared Google client secret is bundled. Distributing a managed OAuth app to customers can require Google's verification; importing a personal client is the current setup method.

YouTube is the only publishing connector in this release. TikTok/Instagram posting and automatic approval of newly generated clips are not implemented.

References: [Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app), [resumable uploads](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol), [video upload and scheduling](https://developers.google.com/youtube/v3/docs/videos/insert).
