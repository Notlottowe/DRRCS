DRRCS

RUNNING THE PROJECT

1. Install Python 3.11 or later with Tkinter.
   The desktop client uses the Python standard library. No pip packages are
   needed to run it.

2. Get client.json and the classroom login details from the project owner.
   They are supplied separately from the source ZIP.

3. Create a folder named .private inside the project folder. Put client.json
   inside it. The file locations should be:

   DRRCS/main.py
   DRRCS/.private/client.json

   client.example.json shows the format but does not contain a working key.

4. On macOS, double-click Start DRRCS.command.
   You can also open Terminal in the project folder and run:

   python3 main.py

   On Windows, open a terminal in the project folder and run:

   python main.py

5. Choose a username, enter its password, and click Sign in.

If you want to keep client.json elsewhere, pass its location when starting:

   python3 main.py --config "/path/to/client.json"

The client connects to the existing Cloudflare backend. A Cloudflare account
is not needed to run the desktop app. An internet connection is required.


ACCOUNTS

dispatcher   Verify citizen reports, update incidents, assign personnel and close resolved incidents.
responder    Progress updates, escalation, resource requests and resolution for assigned incidents.
shelter      Update shelter information and occupancy for assigned shelters.
resource     Allocate requested resources and manage the inventory.
admin        Manage zones, orders, notifications, and reports.
sysadmin     Manage user accounts and view audit history.

Use Switch account in the top-right corner to change accounts.
Browse public shelters and Report an emergency (UC-1, UC-2) are available without signing in.


QUICK INCIDENT DEMO

1. Sign in as dispatcher.
2. Click New incident on the Incidents page.
3. Fill in the form and click Review & save.
4. Check the summary and click Confirm & save.
5. The saved incident appears in the list with its new ID.
6. Sign out and sign in again to check that the record is still there.

Records are stored in Cloudflare D1. Closing the desktop app does not delete
them. Notification delivery is simulated; it does not send real SMS or email.


TESTS

On macOS, double-click Run Tests.command, or run:

   python3 run_tests.py

On Windows:

   python run_tests.py

Open test_results/results.html to see the expected and actual results.
The current backend and API suite has 80 passing tests (UC-1 to UC-24 and client checks). These tests use an
isolated SQLite database and do not change the cloud database.

test_results/cloud_verification.json contains an earlier cloud API test run.
Its terminal check belongs to the old client. It is not a test of the new UI.


FILES TO READ NEXT

TEAM_HANDOFF.txt          Work remaining for UC-1 to UC-12 and merge notes.
docs/IMPLEMENTATION.txt   Existing UC-12 to UC-24 code and data rules.
references/              P2, P3, the project overview, and Phase 4 requirements.

The full UI demonstration still needs to be checked for each role.
