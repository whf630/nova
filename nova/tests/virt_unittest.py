# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
#    Copyright 2010 OpenStack LLC
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from xml.etree.ElementTree import fromstring as xml_to_tree
from xml.dom.minidom import parseString as xml_to_dom

from nova import context
from nova import db
from nova import flags
from nova import test
from nova import utils
from nova.api.ec2 import cloud
from nova.auth import manager
from nova.virt import libvirt_conn

FLAGS = flags.FLAGS
flags.DECLARE('instances_path', 'nova.compute.manager')


class LibvirtConnTestCase(test.TrialTestCase):
    def setUp(self):
        super(LibvirtConnTestCase, self).setUp()
        self.manager = manager.AuthManager()
        self.user = self.manager.create_user('fake', 'fake', 'fake',
                                             admin=True)
        self.project = self.manager.create_project('fake', 'fake', 'fake')
        self.network = utils.import_object(FLAGS.network_manager)
        FLAGS.instances_path = ''

    def test_get_uri_and_template(self):
        ip = '10.11.12.13'

        instance = { 'memory_kb': '1024000',
                     'basepath': '/some/path',
                     'bridge_name': 'br100',
                     'mac_address': '02:12:34:46:56:67',
                     'vcpus': 2,
                     'project_id': 'fake',
                     'bridge': 'br101',
                     'instance_type': 'm1.small'}

        user_context = context.RequestContext(project=self.project,
                                              user=self.user)
        instance_ref = db.instance_create(user_context, instance)
        network_ref = self.network.get_network(user_context)
        self.network.set_network_host(context.get_admin_context(),
                                      network_ref['id'])

        fixed_ip = {'address': ip,
                    'network_id': network_ref['id']}

        ctxt = context.get_admin_context()
        fixed_ip_ref = db.fixed_ip_create(ctxt, fixed_ip)
        db.fixed_ip_update(ctxt, ip, {'allocated': True,
                                      'instance_id': instance_ref['id']})

        type_uri_map = {'qemu': ('qemu:///system',
                             [(lambda t: t.find('.').get('type'), 'qemu'),
                              (lambda t: t.find('./os/type').text, 'hvm'),
                              (lambda t: t.find('./devices/emulator'), None)]),
                        'kvm': ('qemu:///system',
                             [(lambda t: t.find('.').get('type'), 'kvm'),
                              (lambda t: t.find('./os/type').text, 'hvm'),
                              (lambda t: t.find('./devices/emulator'), None)]),
                        'uml': ('uml:///system',
                             [(lambda t: t.find('.').get('type'), 'uml'),
                              (lambda t: t.find('./os/type').text, 'uml')])}

        common_checks = [
            (lambda t: t.find('.').tag, 'domain'),
            (lambda t: t.find('./devices/interface/filterref/parameter').\
                         get('name'), 'IP'),
            (lambda t: t.find('./devices/interface/filterref/parameter').\
                         get('value'), '10.11.12.13')]

        for (libvirt_type, (expected_uri, checks)) in type_uri_map.iteritems():
            FLAGS.libvirt_type = libvirt_type
            conn = libvirt_conn.get_connection(True)

            uri, _template, _rescue = conn.get_uri_and_templates()
            self.assertEquals(uri, expected_uri)

            xml = conn.to_xml(instance_ref)
            tree = xml_to_tree(xml)
            for i, (check, expected_result) in enumerate(checks):
                self.assertEqual(check(tree),
                                 expected_result,
                                 '%s failed check %d' % (xml, i))

            for i, (check, expected_result) in enumerate(common_checks):
                self.assertEqual(check(tree),
                                 expected_result,
                                 '%s failed common check %d' % (xml, i))

        # Deliberately not just assigning this string to FLAGS.libvirt_uri and
        # checking against that later on. This way we make sure the
        # implementation doesn't fiddle around with the FLAGS.
        testuri = 'something completely different'
        FLAGS.libvirt_uri = testuri
        for (libvirt_type, (expected_uri, checks)) in type_uri_map.iteritems():
            FLAGS.libvirt_type = libvirt_type
            conn = libvirt_conn.LibvirtConnection(True)
            uri, _template, _rescue = conn.get_uri_and_templates()
            self.assertEquals(uri, testuri)

    def tearDown(self):
        super(LibvirtConnTestCase, self).tearDown()
        self.manager.delete_project(self.project)
        self.manager.delete_user(self.user)


class IptablesFirewallTestCase(test.TrialTestCase):
    def setUp(self):
        super(IptablesFirewallTestCase, self).setUp()
        
        self.manager = manager.AuthManager()
        self.user = self.manager.create_user('fake', 'fake', 'fake',
                                             admin=True)
        self.project = self.manager.create_project('fake', 'fake', 'fake')
        self.context = context.RequestContext('fake', 'fake')
        self.network = utils.import_object(FLAGS.network_manager)
        self.fw = libvirt_conn.IptablesFirewallDriver()

    def tearDown(self):
        self.manager.delete_project(self.project)
        self.manager.delete_user(self.user)
        super(IptablesFirewallTestCase, self).tearDown()

    def _p(self, *args, **kwargs):
        if 'iptables-restore' in args:
             print ' '.join(args), kwargs['stdin']
        if 'iptables-save' in args:
            return 
    in_rules = ['# Generated by iptables-save v1.4.4 on Mon Dec  6 11:54:13 2010',
                '*filter',
                ':INPUT ACCEPT [969615:281627771]',
                ':FORWARD ACCEPT [0:0]',
                ':OUTPUT ACCEPT [915599:63811649]',
                ':nova-block-ipv4 - [0:0]',
                '-A INPUT -i virbr0 -p udp -m udp --dport 53 -j ACCEPT ',
                '-A INPUT -i virbr0 -p tcp -m tcp --dport 53 -j ACCEPT ',
                '-A INPUT -i virbr0 -p udp -m udp --dport 67 -j ACCEPT ',
                '-A INPUT -i virbr0 -p tcp -m tcp --dport 67 -j ACCEPT ',
                '-A FORWARD -d 192.168.122.0/24 -o virbr0 -m state --state RELATED,ESTABLISHED -j ACCEPT ',
                '-A FORWARD -s 192.168.122.0/24 -i virbr0 -j ACCEPT ',
                '-A FORWARD -i virbr0 -o virbr0 -j ACCEPT ',
                '-A FORWARD -o virbr0 -j REJECT --reject-with icmp-port-unreachable ',
                '-A FORWARD -i virbr0 -j REJECT --reject-with icmp-port-unreachable ',
                'COMMIT',
                '# Completed on Mon Dec  6 11:54:13 2010']

    def test_static_filters(self):
        self.fw.execute = self._p
        instance_ref = db.instance_create(self.context,
                                          {'user_id': 'fake',
                                          'project_id': 'fake'})
        ip = '10.11.12.13'

        network_ref = self.network.get_network(self.context)

        fixed_ip = {'address': ip,
                    'network_id': network_ref['id']}

        admin_ctxt = context.get_admin_context()
        db.fixed_ip_create(admin_ctxt, fixed_ip)
        db.fixed_ip_update(admin_ctxt, ip, {'allocated': True,
                                            'instance_id': instance_ref['id']})


        secgroup = db.security_group_create(admin_ctxt,
                                            {'user_id': 'fake',
                                             'project_id': 'fake',
                                             'name': 'testgroup',
                                             'description': 'test group'})

        db.security_group_rule_create(admin_ctxt,
                                      {'parent_group_id': secgroup['id'],
                                       'protocol': 'tcp',
                                       'from_port': 80,
                                       'to_port': 81,
                                       'cidr': '192.168.10.0/24'})

        db.instance_add_security_group(admin_ctxt, instance_ref['id'],
                                       secgroup['id'])
        instance_ref = db.instance_get(admin_ctxt, instance_ref['id'])

        self.fw.add_instance(instance_ref)

        out_rules = self.fw.modify_rules(self.in_rules)
        
        in_rules = filter(lambda l: not l.startswith('#'), self.in_rules)
        for rule in in_rules:
            if not 'nova' in rule:
                self.assertTrue(rule in out_rules, 'Rule went missing: %s' % rule)

        print '\n'.join(out_rules)

    def est_stuff(self):
        self.fw.execute = self._p
        cloud_controller = cloud.CloudController()
        cloud_controller.create_security_group(self.context,
                                               'testgroup',
                                               'test group description')
        cloud_controller.authorize_security_group_ingress(self.context,
                                                          'testgroup',
                                                          from_port='80',
                                                          to_port='81',
                                                          ip_protocol='tcp',
                                                          cidr_ip='0.0.0.0/0')

        self.fw._apply_ruleset()


class NWFilterTestCase(test.TrialTestCase):

    def setUp(self):
        super(NWFilterTestCase, self).setUp()

        class Mock(object):
            pass

        self.manager = manager.AuthManager()
        self.user = self.manager.create_user('fake', 'fake', 'fake',
                                             admin=True)
        self.project = self.manager.create_project('fake', 'fake', 'fake')
        self.context = context.RequestContext(self.user, self.project)

        self.fake_libvirt_connection = Mock()

        self.fw = libvirt_conn.NWFilterFirewall(lambda:self.fake_libvirt_connection)

    def tearDown(self):
        self.manager.delete_project(self.project)
        self.manager.delete_user(self.user)

    def test_cidr_rule_nwfilter_xml(self):
        cloud_controller = cloud.CloudController()
        cloud_controller.create_security_group(self.context,
                                               'testgroup',
                                               'test group description')
        cloud_controller.authorize_security_group_ingress(self.context,
                                                          'testgroup',
                                                          from_port='80',
                                                          to_port='81',
                                                          ip_protocol='tcp',
                                                          cidr_ip='0.0.0.0/0')

        security_group = db.security_group_get_by_name(self.context,
                                                       'fake',
                                                       'testgroup')

        xml = self.fw.security_group_to_nwfilter_xml(security_group.id)

        dom = xml_to_dom(xml)
        self.assertEqual(dom.firstChild.tagName, 'filter')

        rules = dom.getElementsByTagName('rule')
        self.assertEqual(len(rules), 1)

        # It's supposed to allow inbound traffic.
        self.assertEqual(rules[0].getAttribute('action'), 'accept')
        self.assertEqual(rules[0].getAttribute('direction'), 'in')

        # Must be lower priority than the base filter (which blocks everything)
        self.assertTrue(int(rules[0].getAttribute('priority')) < 1000)

        ip_conditions = rules[0].getElementsByTagName('tcp')
        self.assertEqual(len(ip_conditions), 1)
        self.assertEqual(ip_conditions[0].getAttribute('srcipaddr'), '0.0.0.0')
        self.assertEqual(ip_conditions[0].getAttribute('srcipmask'), '0.0.0.0')
        self.assertEqual(ip_conditions[0].getAttribute('dstportstart'), '80')
        self.assertEqual(ip_conditions[0].getAttribute('dstportend'), '81')
        self.teardown_security_group()

    def teardown_security_group(self):
        cloud_controller = cloud.CloudController()
        cloud_controller.delete_security_group(self.context, 'testgroup')

    def setup_and_return_security_group(self):
        cloud_controller = cloud.CloudController()
        cloud_controller.create_security_group(self.context,
                                               'testgroup',
                                               'test group description')
        cloud_controller.authorize_security_group_ingress(self.context,
                                                          'testgroup',
                                                          from_port='80',
                                                          to_port='81',
                                                          ip_protocol='tcp',
                                                          cidr_ip='0.0.0.0/0')

        return db.security_group_get_by_name(self.context, 'fake', 'testgroup')

    def test_creates_base_rule_first(self):
        # These come pre-defined by libvirt
        self.defined_filters = ['no-mac-spoofing',
                                'no-ip-spoofing',
                                'no-arp-spoofing',
                                'allow-dhcp-server']

        self.recursive_depends = {}
        for f in self.defined_filters:
            self.recursive_depends[f] = []

        def _filterDefineXMLMock(xml):
            dom = xml_to_dom(xml)
            name = dom.firstChild.getAttribute('name')
            self.recursive_depends[name] = []
            for f in dom.getElementsByTagName('filterref'):
                ref = f.getAttribute('filter')
                self.assertTrue(ref in self.defined_filters,
                                ('%s referenced filter that does ' +
                                'not yet exist: %s') % (name, ref))
                dependencies = [ref] + self.recursive_depends[ref]
                self.recursive_depends[name] += dependencies

            self.defined_filters.append(name)
            return True

        self.fake_libvirt_connection.nwfilterDefineXML = _filterDefineXMLMock

        instance_ref = db.instance_create(self.context,
                                          {'user_id': 'fake',
                                          'project_id': 'fake'})
        inst_id = instance_ref['id']

        def _ensure_all_called(_):
            instance_filter = 'nova-instance-%s' % instance_ref['name']
            secgroup_filter = 'nova-secgroup-%s' % self.security_group['id']
            for required in [secgroup_filter, 'allow-dhcp-server',
                             'no-arp-spoofing', 'no-ip-spoofing',
                             'no-mac-spoofing']:
                self.assertTrue(required in
                                self.recursive_depends[instance_filter],
                                "Instance's filter does not include %s" %
                                required)

        self.security_group = self.setup_and_return_security_group()

        db.instance_add_security_group(self.context, inst_id,
                                       self.security_group.id)
        instance = db.instance_get(self.context, inst_id)

        d = self.fw.prepare_instance_filter(instance)
        d.addCallback(_ensure_all_called)
        d.addCallback(lambda _: self.teardown_security_group())

        return d
