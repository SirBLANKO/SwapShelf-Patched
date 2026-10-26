# SwapShelf: Privilege escalation through the profile form

**Location:** `ProfileForm` in `market/forms.py`, the `profile()` view, and the staff check in `desk()` in `market/views.py`.

**Problem:** The profile form lets an ordinary user update Django's `is_staff` field. The checkbox is labelled “Show my name on the department board,” but it changes an authorization flag rather than a display preference.

**Impact:** Any registered user can grant themselves staff status and access the department desk's restricted information. Staff status also satisfies Django admin's staff-status requirement, but it does not automatically grant superuser status or permissions to manage every model.

**Live demo:** A hosted SwapShelf URL has not been provided. The verification described below was performed locally using Django's test client.

## Steps to reproduce

Use the original, unpatched application and a disposable local database.

1. Start the application locally on port 3000 and open `http://localhost:3000`.
2. Register a new ordinary account and sign in.
3. Visit `http://localhost:3000/desk/`. The page should display the message that it is for department board accounts, without showing the restricted note.
4. Open `http://localhost:3000/profile/`.
5. Select **Show my name on the department board** and save the profile.
6. Return to `/desk/`. The restricted department note is now available, even though an administrator never granted the account staff access.

**Expected behavior:** Editing a profile changes only permitted personal information. An ordinary account should remain unable to read the department desk's restricted content.

**Observed behavior before the patch:** A local automated test submitted `is_staff=on` through the profile endpoint. After the request, the user's stored `is_staff` value was `True`. Source inspection confirmed that the desk uses this same flag to authorize access.

**Cause:** The self-service form explicitly includes a privileged model field. `login_required` checks whether the user is signed in, but it does not decide which account properties that user may change. A valid CSRF token does not solve this problem either: a signed-in attacker can submit the form from their own account.

## Code examples

### Vulnerable code: before the patch

In `market/forms.py`:

```python
class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "is_staff"]
        labels = {
            "is_staff": "Show my name on the department board",
        }
```

The profile handler in `market/views.py` saves the submitted form onto the current user:

```python
@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("profile")
    return render(request, "market/profile.html", {"form": form})
```

The desk checks that stored privilege before returning its restricted content:

```python
if not request.user.is_staff:
    return render(request, "market/desk.html", {"allowed": False})
```

The vulnerable flow is:

```text
User submits is_staff=on
    -> ProfileForm accepts is_staff
    -> form.save() updates the user's account
    -> desk() trusts the updated flag
    -> the user gains access to restricted content
```

### Patched code: after the fix

In `market/forms.py`:

```python
class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]
```

The form now uses an explicit allowlist of ordinary profile fields. `is_staff` is no longer rendered or saved by this form. Even if someone manually adds `is_staff=on` to a request, it is not a writable field in `ProfileForm`.

**Fix:** Remove `is_staff` and its misleading label from the profile form, and update the profile page description. Keep the existing staff check in the desk view. Legitimate staff members retain their privileges when editing their names or email addresses.

**Observed behavior after the patch:** The regression test submitted both `is_staff=on` and `is_superuser=on`. The ordinary account remained nonstaff and nonsuperuser, while its permitted name update succeeded. The desk continued to withhold restricted content from that account.

## Exact test request

The following shows the relevant request to the local profile endpoint. The session cookie and CSRF token must come from the same logged-in test account; they are intentionally omitted here.

```http
POST /profile/ HTTP/1.1
Host: localhost:3000
Content-Type: application/x-www-form-urlencoded

first_name=Updated&last_name=Name&email=test%40example.com&is_staff=on&is_superuser=on&csrfmiddlewaretoken=<valid-token>
```

**Before the patch:** `is_staff` becomes true. `is_superuser` is not a field in the original form and is not granted by this request.

**After the patch:** Both privilege fields remain false for an ordinary account, and the permitted personal information is updated. A successful redirect alone does not prove the fix; the test checks the saved account state and desk access.

## Additional security fixes

The review also identified related authorization problems and a startup configuration risk. These were repaired in the same deliverable.

| Area | Original problem | Repair |
|---|---|---|
| Listing edit, sold, and delete | Any logged-in user could select another owner's listing ID. The edit form also exposed its private seller note. | Each lookup now includes `owner=request.user`; unauthorized requests return 404 before reading the form or changing data. |
| Private message threads | Any logged-in user could read or reply to a thread by supplying its ID. | The lookup allows only the thread's buyer or the listing's owner. |
| Starting a conversation | A GET request created a thread, allowing navigation to cause an unwanted state change. | Require POST and use a form with a CSRF token. |
| Demo accounts | Docker startup automatically seeded accounts with publicly documented passwords. | Remove automatic seeding; retain the explicit seed command for disposable local demos. |

For example, the listing handlers now use:

```python
listing = get_object_or_404(Listing, pk=pk, owner=request.user)
```

The thread handler now checks participation before either displaying or creating messages:

```python
threads = Thread.objects.select_related("listing", "buyer", "listing__owner").filter(
    Q(buyer=request.user) | Q(listing__owner=request.user)
)
thread = get_object_or_404(threads, pk=pk)
```

Hiding controls in the interface is only a usability improvement. The server-side checks enforce these restrictions even when someone sends requests directly.

## Security and normal functionality checks

The following results come from the 12 automated tests in `market/tests.py`, run against the patched application with synthetic accounts and a disposable test database.

| Test | Actual result |
|---|---|
| Ordinary profile update with forged privilege fields | Name updated; staff and superuser flags remained false; restricted desk content stayed unavailable. |
| Legitimate staff profile update | Staff status and desk access were preserved. |
| Nonowner listing access | Edit GET and edit/sold/delete POST requests returned 404; listing data remained unchanged. |
| Owner listing management | Owner could read the edit form, edit the listing, mark it sold, and remove it. |
| Public listing page and owner controls | Public detail remained available; private notes were absent; edit controls were hidden from nonowners. |
| Unrelated user accessing a thread | Read and reply requests returned 404; no additional message was created. |
| Buyer and seller using a thread | Both could read and reply; a forged sender field did not change the recorded sender. |
| Conversation creation method and CSRF | GET and HEAD returned 405; POST without a CSRF token returned 403; valid POST created or reused the caller's thread. |
| Creating a listing | Posting succeeded and ownership was assigned to the authenticated user. |
| Anonymous access to protected routes | Requests redirected to sign-in. |
| Automatic demo accounts | The test database contained no demo users after migration setup. |
| Explicit demo seeding | The seed command remained available and created demo data when invoked. |

Run the checks from the project directory after installing its requirements:

```bash
python manage.py test market --verbosity 1
python manage.py makemigrations --check --dry-run
```

**Verification status:** All 12 tests passed using Python 3.12.14 and Django 5.1.7. Django system checks reported no issues. The migration check reported no changes detected, so no schema migration is required.

## Hosted testing and existing accounts

No hosted deployment or Docker image was tested. The results above establish the local application behavior, not the security of a hosting provider or production configuration. No browser screenshots were captured for these tests.

Updating the code does not revoke staff status that was already granted or remove existing demo accounts. An administrator should review staff assignments and disable or reset demo accounts before using real data. Production secrets, allowed hosts, HTTPS settings, and dependency updates also need deployment-specific review.

## Summary

The profile privilege-escalation bug was fixed by removing authorization fields from the self-service form. Ordinary users can still edit their personal information, but cannot use that form to grant themselves staff access. Additional checks now protect listing ownership and private conversations, and conversation creation requires a CSRF-protected POST. All 12 local regression tests passed after the repairs.
