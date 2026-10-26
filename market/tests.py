from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from market.models import Listing, Message, Thread


class AccessControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user('owner', password='test-password')
        cls.buyer = User.objects.create_user('buyer', password='test-password')
        cls.outsider = User.objects.create_user('outsider', password='test-password')
        cls.staff = User.objects.create_user('staff', password='test-password', is_staff=True)
        cls.listing = Listing.objects.create(owner=cls.owner, title='Book',
            description='A book', price='10.00', seller_note='PRIVATE TEST NOTE')
        cls.thread = Thread.objects.create(listing=cls.listing, buyer=cls.buyer)
        Message.objects.create(thread=cls.thread, sender=cls.owner, body='PRIVATE TEST MESSAGE')

    def listing_data(self):
        return dict(title='Updated', description='Updated description', price='12.00',
                    category='books', seller_note='Updated private note', owner=self.outsider.pk)

    def test_profile_cannot_grant_privileges(self):
        self.client.force_login(self.outsider)
        self.client.post(reverse('profile'), dict(first_name='Updated', last_name='Name',
            email='test@example.com', is_staff='on', is_superuser='on'))
        self.outsider.refresh_from_db()
        self.assertFalse(self.outsider.is_staff)
        self.assertFalse(self.outsider.is_superuser)
        self.assertEqual(self.outsider.first_name, 'Updated')
        self.assertNotContains(self.client.get(reverse('profile')), 'name="is_staff"')
        self.assertFalse(self.client.get(reverse('desk')).context['allowed'])

    def test_staff_profile_keeps_trusted_privilege(self):
        self.client.force_login(self.staff)
        self.client.post(reverse('profile'), dict(first_name='Staff', last_name='', email=''))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_staff)
        self.assertTrue(self.client.get(reverse('desk')).context['allowed'])

    def test_nonowners_cannot_read_or_change_listing(self):
        for user in (self.outsider, self.buyer, self.staff):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                response = self.client.get(reverse('listing_edit', args=[self.listing.pk]))
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(b'PRIVATE TEST NOTE', response.content)
                for route in ('listing_edit', 'listing_sold', 'listing_delete'):
                    self.assertEqual(self.client.post(reverse(route, args=[self.listing.pk]),
                        self.listing_data()).status_code, 404)
                self.listing.refresh_from_db()
                self.assertEqual(self.listing.title, 'Book')
                self.assertEqual(self.listing.status, 'available')
                self.assertEqual(self.listing.seller_note, 'PRIVATE TEST NOTE')

    def test_owner_can_manage_listing(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('listing_edit', args=[self.listing.pk])), 'PRIVATE TEST NOTE')
        for route in ('listing_edit', 'listing_sold', 'listing_delete'):
            self.assertEqual(self.client.post(reverse(route, args=[self.listing.pk]),
                self.listing_data()).status_code, 302)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.title, 'Updated')
        self.assertEqual(self.listing.owner, self.owner)
        self.assertEqual(self.listing.status, 'removed')

    def test_public_detail_and_owner_controls(self):
        url = reverse('listing_detail', args=[self.listing.pk])
        self.assertContains(self.client.get(url), 'Book')
        self.client.force_login(self.outsider)
        response = self.client.get(url)
        self.assertNotContains(response, reverse('listing_edit', args=[self.listing.pk]))
        self.assertNotContains(response, 'PRIVATE TEST NOTE')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url), reverse('listing_edit', args=[self.listing.pk]))

    def test_nonparticipants_cannot_read_or_reply(self):
        for user in (self.outsider, self.staff):
            self.client.force_login(user)
            url = reverse('thread_detail', args=[self.thread.pk])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(b'PRIVATE TEST MESSAGE', response.content)
            self.assertEqual(self.client.post(url, {'body': 'Intrusion'}).status_code, 404)
        self.assertEqual(self.thread.messages.count(), 1)

    def test_participants_can_read_and_reply(self):
        for user in (self.owner, self.buyer):
            self.client.force_login(user)
            url = reverse('thread_detail', args=[self.thread.pk])
            self.assertContains(self.client.get(url), 'PRIVATE TEST MESSAGE')
            self.assertEqual(self.client.post(url, {'body': 'Reply', 'sender': self.staff.pk}).status_code, 302)
            self.assertEqual(self.thread.messages.order_by('pk').last().sender, user)

    def test_start_thread_requires_post_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.outsider)
        url = reverse('start_thread', args=[self.listing.pk])
        before = Thread.objects.count()
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.head(url).status_code, 405)
        self.assertEqual(client.post(url).status_code, 403)
        self.assertEqual(Thread.objects.count(), before)
        client.get(reverse('listing_detail', args=[self.listing.pk]))
        token = client.cookies['csrftoken'].value
        for _ in range(2):
            self.assertEqual(client.post(url, {'csrfmiddlewaretoken': token}).status_code, 302)
        self.assertEqual(Thread.objects.count(), before + 1)
        self.assertTrue(Thread.objects.filter(listing=self.listing, buyer=self.outsider).exists())

    def test_posting_still_assigns_authenticated_owner(self):
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.post(reverse('listing_new'), self.listing_data()).status_code, 302)
        self.assertEqual(Listing.objects.get(title='Updated').owner, self.buyer)

    def test_anonymous_protected_routes_require_login(self):
        for route, args in [('profile', []), ('desk', []), ('listing_new', []),
                            ('listing_edit', [self.listing.pk]), ('listing_sold', [self.listing.pk]),
                            ('listing_delete', [self.listing.pk]), ('thread_detail', [self.thread.pk]),
                            ('start_thread', [self.listing.pk])]:
            self.assertEqual(self.client.get(reverse(route, args=args)).status_code, 302)

    def test_demo_accounts_are_not_created_by_migrations(self):
        self.assertFalse(User.objects.filter(username__in=['alex', 'jordan', 'sam']).exists())

    def test_explicit_demo_seed_remains_available(self):
        call_command('seed', verbosity=0)
        self.assertTrue(User.objects.filter(username='alex').exists())
