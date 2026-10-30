# Changelog

What changed in the app, newest first. Each release's notes on GitHub list what it added.

## 2026-10-30

### Mac app
- A menu-bar item while the app is open, with the Dock icon still there (quitting either quits the app)
- The menu lists runs going or waiting, recently finished runs (about 45 seconds), résumés ready to send, and
  newest roles at a fit-score floor since yesterday; click a row to open it in the browser
- The number on the item is how many runs are going or waiting; a middle dot means résumés ready or new high
  fits with no runs
- On macOS Tahoe and later, if the oyster is missing from the menu bar, turn Job Oyster on under
  System Settings → Menu Bar → Allow in the Menu Bar
- **Check for Updates…** in the menu bar downloads a signed release and replaces this copy of the app; the
  profile folder (`~/JobOyster`) is left as it is. An unsigned release still opens the releases page

### Profile
- A **Menu Bar Settings** card sets the fit floor for newest roles in the menu bar (1–9, 7 by default;
  `menu.fit_floor` in `searches.yaml`). 7 matches Fit 7+ in the job list
- When the page can't reach the server, it says to open the Mac app again, or `oyster run` from the command line

### Running from code
- `oyster run` still starts the server and opens the browser only; it does not open the Mac app or the menu bar

## 2026-10-29

### Profile
- The AI card shows whether Claude is ready, and the app finds Claude Code again when it moves

## 2026-10-28

### Finding jobs
- Filters for the companies you want, and the ones you don't

## 2026-10-26

### Finding jobs
- Comeet and Workable postings are read, and a closed posting says so

## 2026-10-24

### Dashboard
- Application flow, and a card to share your search

### Profile
- A setup wizard for your first run
- See and edit the pinned résumé section

### Tracker
- An Open in Notion link beside Sync

### App
- The top tabs in a clearer order

### Startups
- Startup roles get their posting text from its link

## 2026-10-19

### Résumés
- A pinned section in every résumé

## 2026-10-17

### Finding jobs
- HiringCafe searches, checked on every run

### Adding a job
- Links the app can't read say why

### App
- About Job Oyster, with the version, the guides and GitHub

### Running from code
- `oyster start` runs the app in the background
- The app can keep itself up to date

## 2026-10-16

### Project
- The project is now at github.com/Alexander-C-Carnes/job-oyster-app

### Résumés
- PDF file names can use the date and the version
- Clearer note when the evidence models aren't used

## 2026-10-14

### Résumé
- Uploads get scored

### Name
- A new icon
- Job Search is now Job Oyster

### Tracker
- A Ghosted status, set on its own after 60 days

### Mac app
- Each release says what's new
- The release signer runs outside Documents
- New releases get signed on their own
