# Public directory

## Browsing

- Search mic names, venues, addresses, boroughs and neighborhoods.
- Filter by day, borough, entry fee, signup method and host status (All, Host
  claimed, Venue confirmed, Unclaimed); sort by time, fee or name. Purchase minimums
  are shown separately from entry fees.
- A calendar picks an exact date. Weekly, biweekly and one-time ("pop-up") mics are
  labeled; biweekly mics without a confirmed date stay visible with a note.
- On phones, swipe vertically through every matching mic in one continuous list.
  Search and filters remain above the list. Choose **Mics**, **Map**, or **Both**
  to use the available space for the list, map, or the two together.
  Desktop results retain Previous/Next pages.
- **Menu → Text size** offers Small, Standard, Large and Extra large text for mic
  listings, details and map labels. Row heights grow with the text; the preference
  is saved in the current browser. Navigation and filters keep their compact size.
- While the map is visible, the list follows the area in view. Pan or zoom to narrow
  results; zoom out or use **NYC** to see the whole city. Hiding the map restores the
  full filtered list, including mics without coordinates.
- Mics whose start time has passed today in `America/New_York` appear dimmer in
  both the list and map pins, and remain selectable. Upcoming starts, other dates
  and unconfirmed dates stay bright. Dimming refreshes automatically as time passes.
- Today's upcoming mics appear first; today's earlier starts move to the bottom.
  Other dates remain between those groups. The selected time, fee or name sort
  applies within each group. Ordering uses New York time and updates automatically
  without moving the map or resetting the list's scroll position.
- Mic details use **Mic**, **Hosts**, **Notes**, **Sources** and **Link** tabs; long
  notes and source lists are paginated.
- Saved mics are stored only in the visitor's browser. CSV export is available.
- Source links, last-check timestamps, conflicts, stale warnings and curated edits are
  visible. A checked source is never treated as host confirmation.
- **LAST SYNC AT** shows the latest successful online-source check in New York time.
- Repeated generated Comediq dates for the same weekly session appear once.
  Historical source records, original links and host edits are retained; distinct
  times, real dated events and unconfirmed schedules remain separate.
- Laughing Buddha's source follows its official registration calendar and imports
  explicit dated in-person mics, including temporary schedule changes. Online mics,
  classes and regular shows are excluded. Each check requires a complete valid
  calendar; failed checks keep the previously imported entries.

## Map

`assets/map.js` is a small, dependency-free Web Mercator map that fetches only the
visible OpenStreetMap tiles, with visible attribution and normal browser caching.
No tiles are bundled or prefetched; pins and the list still work if tiles fail.

- The initial view fits the five boroughs, with borough labels at city scale.
- Each mic has its own pin at its supplied coordinates.
- Unmapped mics are available with the map hidden; cancelled or postponed mics have
  no active pin.
- Pins are color-coded, with a visible legend.

### Touch gestures

One-finger drag, two-finger pinch/pan, double-tap zoom, and zoom controls. Pinch zoom
stays anchored between the fingers, requesting integer-zoom tiles with fractional
visual scaling. Dragging from a pin does not open it, and touch updates render at most
once per animation frame.

On coarse-pointer devices, map button actions and completed pinch zooms trigger a
brief vibration where `navigator.vibrate` exists. Reduced-motion preferences disable
it; unsupported browsers simply skip it.

## Visitor counts

`/` and `/about` send one first-party count request per page load. A random
HttpOnly, SameSite=Lax cookie lasts one year; only its SHA-256 hash and first/last-seen
timestamps are stored. No IP addresses, identities or page histories are kept.
Signed-in admin/host browsers, obvious bots, and browsers sending Do Not Track or
Global Privacy Control are excluded.

The admin dashboard shows estimated unique browsers for today, the last 7 and 30
New York calendar days, and all time. A compact public total is shown in the header.
Counts are approximate (cleared cookies or multiple devices count more than once)
and use the existing database rather than a third-party analytics service.

## Shop

`/shop` renders merchandise from a Fourthwall storefront with variants and a cart,
handing off to Fourthwall's hosted checkout. The server only exposes the public
storefront token (`FOURTHWALL_STOREFRONT_TOKEN`); the page reports the shop as
unavailable when it is not configured.
