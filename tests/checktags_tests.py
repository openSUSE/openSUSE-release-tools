# -*- coding: utf-8 -*-
# Copyright (C) 2015 SUSE Linux GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
#
# To run this test manually, go to the parent directory and run:
# LANG=C python tests/checktags_tests.py

import os
import unittest
import logging
import httpretty
import osc
import re
import urlparse
import sys
sys.path.append(".")

from check_tags_in_requests import TagChecker

APIURL = 'https://maintenancetest.example.com'
FIXTURES = os.path.join(os.getcwd(), 'tests/fixtures')

class TestTagChecker(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """

        httpretty.reset()
        httpretty.enable()

        oscrc = os.path.join(FIXTURES, 'oscrc')
        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        #osc.conf.config['debug'] = 1

        logging.basicConfig()
        self.logger = logging.getLogger(__file__)
        self.logger.setLevel(logging.DEBUG)

        self.checker = TagChecker(apiurl = APIURL, \
                user = 'maintbot', \
                logger = self.logger)

    def run_test_1_changes_file(self, diffsupplement='', accept=False):
        httpretty.register_uri(httpretty.GET,
                               osc.core.makeurl(APIURL, ['source', "openSUSE:Factory", "nano", '_meta'], {}),
                               match_querystring = True,
                               body = """<package name="nano" project="openSUSE:Factory">
  <title>Pico Editor Clone with Enhancements</title>
  <description>GNU nano is a small and friendly text editor. It aims to emulate the
Pico text editor while also offering a few enhancements.</description>
  <devel project="editors" package="nano"/>
</package>""")

        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/293129",
            match_querystring = True,
            body = """
                <request id="293129">
                  <action type="submit">
                    <source project="editors" package="nano" rev="25"/>
                    <target project="openSUSE:Factory" package="nano"/>
                  </action>
                  <state name="review" who="factory-auto" when="2015-03-25T16:24:59">
                    <comment>Please review build success</comment>
                  </state>
                  <review state="accepted" when="2015-03-25T16:24:32" who="licensedigger" by_group="legal-auto">
                    <comment></comment>
                    <history who="licensedigger" when="2015-03-25T16:30:13">
                      <description>Review got accepted</description>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:32" who="factory-auto" by_group="factory-auto">
                    <comment>Check script succeeded</comment>
                    <history who="factory-auto" when="2015-03-25T16:24:59">
                      <description>Review got accepted</description>
                      <comment>Check script succeeded</comment>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:32" who="coolo" by_group="factory-staging">
                    <comment>No need for staging, not in tested ring projects.</comment>
                    <history who="coolo" when="2015-03-25T20:47:33">
                      <description>Review got accepted</description>
                      <comment>No need for staging, not in tested ring projects.</comment>
                    </history>
                  </review>
                  <review state="new" by_user="maintbot">
                    <comment>Please review sources</comment>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:59" who="factory-repo-checker" by_user="factory-repo-checker">
                    <comment>Builds for repo editors/openSUSE_Factory</comment>
                    <history who="factory-repo-checker" when="2015-03-25T18:28:47">
                      <description>Review got accepted</description>
                      <comment>Builds for repo editors/openSUSE_Factory</comment>
                    </history>
                  </review>
                </request>
            """)
        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/293129?withhistory=1",
            match_querystring = True,
            body = """
                <request id="293129">
                  <action type="submit">
                    <source project="editors" package="nano" rev="25"/>
                    <target project="openSUSE:Factory" package="nano"/>
                  </action>
                  <state name="review" who="factory-auto" when="2015-03-25T16:24:59">
                    <comment>Please review build success</comment>
                  </state>
                  <review state="accepted" when="2015-03-25T16:24:32" who="licensedigger" by_group="legal-auto">
                    <comment></comment>
                    <history who="licensedigger" when="2015-03-25T16:30:13">
                      <description>Review got accepted</description>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:32" who="factory-auto" by_group="factory-auto">
                    <comment>Check script succeeded</comment>
                    <history who="factory-auto" when="2015-03-25T16:24:59">
                      <description>Review got accepted</description>
                      <comment>Check script succeeded</comment>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:32" who="coolo" by_group="factory-staging">
                    <comment>No need for staging, not in tested ring projects.</comment>
                    <history who="coolo" when="2015-03-25T20:47:33">
                      <description>Review got accepted</description>
                      <comment>No need for staging, not in tested ring projects.</comment>
                    </history>
                  </review>
                  <review state="new" by_user="maintbot">
                    <comment>Please review sources</comment>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:59" who="factory-repo-checker" by_user="factory-repo-checker">
                    <comment>Builds for repo editors/openSUSE_Factory</comment>
                    <history who="factory-repo-checker" when="2015-03-25T18:28:47">
                      <description>Review got accepted</description>
                      <comment>Builds for repo editors/openSUSE_Factory</comment>
                    </history>
                  </review>
                  <history who="darix" when="2015-03-25T16:24:32">
                    <description>Request created</description>
                  </history>
                  <history who="factory-auto" when="2015-03-25T16:24:59">
                    <description>Request got a new review request</description>
                    <comment>Please review sources</comment>
                  </history>
                  <history who="factory-auto" when="2015-03-25T16:24:59">
                    <description>Request got a new review request</description>
                    <comment>Please review build success</comment>
                  </history>
                </request>
            """)
        self.req_293129_diff = '''
changes files:
--------------
--- nano.changes
+++ nano.changes
@@ -1,0 +2,110 @@
+Mon Mar 23 20:10:23 UTC 2015 - sor.alexei@meowr.ru
+ 
+- Update to 2.4.0:       ''' + diffsupplement + '''
+  * doc/nanorc.sample.in: Remove ‘undo’ section which is now obsolete.
+  * doc/syntax/nanorc.nanorc: Remove 'undo' from the valid options.
+  * doc/texinfo/nano.texi, doc/man/nanorc.5: Typo fix.
+  * src/global.c (add_to_sclist): Remove the now unused and unneeded
+    addition ability from this builder function of the shortcut list.
+  * src/global.c (strtokeytype): Move this to a better place.
+  * src/global.c (first_sc_for): Move this too to a better place.
+  * src/prompt.c (do_yesno_prompt): Use the new and more direct
+    func_from_key() wrapper instead of get_shortcut().
+  * src/text.c (do_linter): Likewise.
+  * src/files.c (do_insertfile, do_writeout): Likewise.
+  * src/files.c (do_insertfile): Adjust some indentation.
+  * src/prompt.c (do_statusbar_input), src/browser.c (do_browser):
+    Reorder a few things, and adjust some whitespace.
+  * doc/man/nano.1, doc/man/rnano.1: Separate short and long option
+    by a comma instead of putting the long one between parentheses.
+    And showing the required quotes around the argument of -Q.
+  * doc/texinfo/nano.texi: Standardize the formatting of command-line
+    options -- each one separately. Also add some more markup.
+  * doc/man/nano.1, doc/man/rnano.1: Tweak the formatting a bit so
+    that po4a will create a nicer POT file.
+  * doc/man/nanorc.5: Improve some of the wordings and formatting.
+  * doc/syntax/nanorc.nanorc: Remove a mistaken OR which causes a
+    'Bad regex, empty (sub)expression' error on some systems.
+  * doc/texinfo/nano.texi: Improve some wordings and formatting.
+  * src/text.c (do_justify): Replace the old get_shortcut() wrapper
+    with the new func_from_key().
+  * doc/syntax/{perl,python,ruby,sh}.nanorc: Recognize also header
+    lines of the form "#!/usr/bin/env thing" besides "#!/bin/thing".
+  * doc/syntax/spec.nanorc: Colorize %pretrans and %posttrans fully.
+  * src/files.c (do_lockfile): Gettextize the "File being edited"
+    prompt, and improve its wording.
+  * src/winio.c (do_credits): Remove the names of past translators
+    from the Easter-egg scroll.
+  * THANKS: Add a missing historical translator name.
+  * src/winio.c (do_credits): Add Mark to the scroll, for all his
+    undo work, colouring nano's interface, and other patches.
+  * New formatter code to support syntaxes like
+    go which have tools to automatically lint and reformat the text
+    for you (gofmt), which is lovely. rcfile option formatter,
+    function text.c:do_formatter() and some other calls.
+  * src/files.c (open_buffer): Check here for locking and properly
+    handle choosing to not open a file when locked instead of in
+    open_file().
+  * src/winio.c (do_credits): Add a general entry for all translators.
+  * src/files.c (write_lockfile): Avoid writing uninitialized bytes
+    to the lock file -- a simple null_at() would not initialize the
+    buffer.
+  * src/files.c (do_lockfile): Make sure that 'lockprog' and
+    'lockuser' are terminated -- strncpy() does not guarantee that
+    on its own.
+  * src/files.c (do_lockfile): Avoid printing a wrong PID on the
+    status bar due to treating serialized PID bytes as signed
+    integers.
+  * src/files.c (write_lockfile): Do not trim the nano version
+    number -- snprintf() counts the trailing zero into the size limit.
+  * src/cut.c (do_cut_text): Make sure to set modified even when
+    using --enable-tiny.
+  * src/file.c (do_lockfile): Also show the name of the affected file
+    when finding a lock file, for when many files are opened at once.
+  * src/file.c (do_lockfile): The user does the editing, not the editor.
+  * doc/syntax/sh.nanorc: Recognize also dash, openrc and runscript.
+  * README: Fix the explanation of how to subscribe to a mailing list.
+  * doc/syntax/{java,lua,python,ruby}.nanorc: Wrap some overlong lines.
+  * src/rcfile.c (parse_binding): Add an exception for do_toggle() as
+    rebinding toggles broke with r5022. (Fixed in r5134.)
+  * doc/man/nanorc.5, doc/texinfo/nano.texi: Add a note about the
+    inherent imperfection of using regular expressions for syntax
+    highlighting.
+  * doc/man/nanorc.5: Improve the indentation of some lists.
+  * doc/man/nanorc.5, doc/texinfo/nano.texi: Remove the mistaken
+    square brackets around the arguments of "header" and "magic" --
+    those arguments are not optional. Also add "formatter" to the
+    texinfo document, and slightly improve its punctuation.
+  * src/proto.h, src/nano.c: Fix compilation with --enable-tiny plus
+    --enable-nanorc.
+  * src/rcfile.c (parse_binding): Fix the rebinding of toggles.
+  * doc/man/{nano.1,rnano.1,nanorc.5}, doc/texinfo/nano.texi: Update
+    years and version numbers in the docs in anticipation of a release.
+  * src/nano.c (version): Drop compile time from version information
+    to enable a reproducible build.
+  * src/nano.c (renumber): Get out if there is nothing to renumber,
+    to prevent do_undo() from falling over trying to renumber emptiness.
+  * src/text.c (do_formatter): Fix a message plus a few comments.
+  * src/text.c (do_alt_speller): Do not set the modified flag when
+    an external spell checker didn't make any changes.
+  * src/nano.c (finish_stdin_pager, cancel_stdin_pager, stdin_pager):
+    Normalize the whitespace, remove an old comment, and place another
+    one better.
+  * src/text.c (do_undo): Make a message equal to another one. It
+    was mistakenly changed in r4950. (This is translation-neutral.)
+  * src/global.c (shortcut_init): Keep related items together in the
+    ^G help screen.
+  * src/text.c (do_alt_speller): Restore the positions of the mark
+    and the cursor in a better way: to the columns where they were.
+  * src/text.c (do_alt_speller): Remove some leftovers.
+  * src/search.c: Place some comments better and unwrap some lines.
+  * src/chars.c (move_mbleft): Start looking for a multibyte char
+    not at the start of the string, but only as far back as such a
+    char can possibly be. Change suggested by Mark Majeres.
+  *  src/search.c (findnextstr): Step backward or forward not simply
+    one byte but one character (possibly multibyte).
+  * src/winio.c (edit_redraw): Do not center the current line when
+    smooth scrolling is used.
+- Do less manually in spec.
+
+-------------------------------------------------------------------

old:
----
  nano-2.3.6.tar.gz

new:
----
  nano-2.4.0.tar.gz

spec files:
-----------
--- nano.spec
+++ nano.spec
@@ -1,7 +1,7 @@
 #
 # spec file for package nano
 #
-# Copyright (c) 2014 SUSE LINUX Products GmbH, Nuernberg, Germany.
+# Copyright (c) 2015 SUSE LINUX GmbH, Nuernberg, Germany.
 #
 # All modifications and additions to the file contributed by third parties
 # remain the property of their copyright owners, unless otherwise agreed
@@ -16,15 +16,22 @@
 #
 
 
+%define _version 2.4
 Name:           nano
-Version:        2.3.6
+Version:        2.4.0
 Release:        0
-Summary:        Pico Editor Clone with Enhancements
+Summary:        Pico editor clone with enhancements
 License:        GPL-3.0+
 Group:          Productivity/Editors/Other
-Url:            http://www.nano-editor.org/
-Source0:        http://www.nano-editor.org/dist/v2.3/%{name}-%{version}.tar.gz
+Url:            http://nano-editor.org/
+Source0:        http://nano-editor.org/dist/v%{_version}/%{name}-%{version}.tar.gz
 BuildRequires:  file-devel
+BuildRequires:  ncurses-devel
+BuildRequires:  pkg-config
+Requires(post): info
+Requires(preun): info
+Recommends:     %{name}-lang = %{version}
+BuildRoot:      %{_tmppath}/%{name}-%{version}-build
 %if 0%{?suse_version} > 1230
 BuildRequires:  groff-full
 %else
@@ -35,69 +42,58 @@
 %else
 BuildRequires:  texinfo
 %endif
-BuildRequires:  ncurses-devel
-BuildRequires:  pkg-config
-Requires(post): info
-Requires(preun): info
-Recommends:     %{name}-lang = %{version}
-BuildRoot:      %{_tmppath}/%{name}-%{version}-build
 
 %description
-GNU nano is a small and friendly text editor. It aims to emulate the
-Pico text editor while also offering a few enhancements.
+GNU nano is a small and friendly text editor. It aims to emulate
+the Pico text editor while also offering a few enhancements.
 
 %lang_package
 
 %prep
 %setup -q
 
-# Remove build time references so build-compare can do its work
+# Remove build time references so build-compare can do its work.
 FAKE_BUILDTIME=$(LC_ALL=C date -u -r %{_sourcedir}/%{name}.changes '+%%H:%%M')
 FAKE_BUILDDATE=$(LC_ALL=C date -u -r %{_sourcedir}/%{name}.changes '+%%b %%e %%Y')
 sed -i "s/__TIME__/\"$FAKE_BUILDTIME\"/" src/nano.c
 sed -i "s/__DATE__/\"$FAKE_BUILDDATE\"/" src/nano.c
 
 %build
-%configure --disable-rpath --enable-utf8
+%configure \
+  --disable-rpath \
+  --enable-utf8
 make %{?_smp_mflags}
 
 %install
-%makeinstall
+%make_install
 
-# Remove doc files that should be in defaultdocdir
-rm -rf %{buildroot}%{_datadir}/nano/man-html/
-rm -rf %{buildroot}%{_datadir}/doc/nano/
-
-# Manually install the doc files in order to easily split them between the main and lang package
-install -dpm 0755 %{buildroot}%{_defaultdocdir}/nano
-install -pm 0644 AUTHORS COPYING COPYING.DOC ChangeLog ChangeLog.pre-2.1 NEWS README THANKS TODO UPGRADE %{buildroot}%{_defaultdocdir}/nano/
-install -pm 0644 doc/faq.html doc/nanorc.sample %{buildroot}%{_defaultdocdir}/nano/
-install -dpm 0755 %{buildroot}%{_defaultdocdir}/nano/man-html/fr
-install -pm 0644 doc/man/*.html %{buildroot}%{_defaultdocdir}/nano/man-html/
-install -pm 0644 doc/man/fr/*.html %{buildroot}%{_defaultdocdir}/nano/man-html/fr/
+# Move documents to a proper directory.
+mkdir -p %{buildroot}%{_docdir}/
+mv -f %{buildroot}%{_datadir}/doc/%{name}/ %{buildroot}%{_docdir}/%{name}/
 
 %find_lang %{name} --with-man --all-name
 
 %post
-%install_info --info-dir=%{_infodir} %{_infodir}/%{name}.info%{ext_info}
+%install_info --info-dir=%{_infodir} %{_infodir}/%{name}.info%{?ext_info}
 
 %preun
-%install_info_delete --info-dir=%{_infodir} %{_infodir}/%{name}.info%{ext_info}
+%install_info_delete --info-dir=%{_infodir} %{_infodir}/%{name}.info%{?ext_info}
 
 %files
-%defattr(-,root,root,-)
-%doc %{_defaultdocdir}/nano/
-%exclude %{_defaultdocdir}/nano/man-html/fr/
+%defattr(-,root,root)
+%doc AUTHORS ChangeLog ChangeLog.pre-2.1 COPYING COPYING.DOC NEWS README THANKS TODO UPGRADE
+%doc %{_docdir}/nano/
+%exclude %{_docdir}/%{name}/*/
 %{_bindir}/nano
 %{_bindir}/rnano
-%doc %{_infodir}/nano.info%{ext_info}
-%doc %{_mandir}/man1/nano.1%{ext_man}
-%doc %{_mandir}/man1/rnano.1%{ext_man}
-%doc %{_mandir}/man5/nanorc.5%{ext_man}
 %{_datadir}/nano/
+%{_infodir}/nano.info%{?ext_info}
+%{_mandir}/man1/nano.1%{?ext_man}
+%{_mandir}/man1/rnano.1%{?ext_man}
+%{_mandir}/man5/nanorc.5%{?ext_man}
 
 %files lang -f %{name}.lang
-%defattr(-,root,root,-)
-%doc %{_defaultdocdir}/nano/man-html/fr/
+%defattr(-,root,root)
+%doc %{_docdir}/%{name}/*/
 
 %changelog

other changes:
--------------

++++++ nano-2.3.6.tar.gz -> nano-2.4.0.tar.gz
(75642 lines skipped)
'''

        result = { 'state_accepted' : None }

        def change_request(result, method, uri, headers):
            u = urlparse.urlparse(uri)
            if u.query == 'cmd=diff':
                return (200, headers, self.req_293129_diff)
            if u.query == 'newstate=accepted&cmd=changereviewstate&by_user=maintbot':
                result['state_accepted'] = True
            elif u.query == 'newstate=declined&cmd=changereviewstate&by_user=maintbot':
                result['state_accepted'] = False
            return (200, headers, '<status code="ok"/>')

        httpretty.register_uri(httpretty.POST,
            APIURL + "/request/293129",
            body = lambda method, uri, headers: change_request(result, method, uri, headers))

        self.checker.set_request_ids(['293129'])
        self.checker.check_requests()

        self.assertEqual( result['state_accepted'], accept )


    def test_decline_request_1_changes_file(self):
        # .changes without tags
        self.run_test_1_changes_file( accept=False )

    def test_accept_request_1_changes_file(self):
        # .changes with correct tag
        self.run_test_1_changes_file( diffsupplement='bnc#1234', accept=True )

    def test_decline_request_1_changes_file2(self):
        # .changes with malformed tag
        self.run_test_1_changes_file( diffsupplement='bnc#-', accept=False )

    def test_accept_request_1_changes_file2(self):
        # .changes with another correct tag
        self.run_test_1_changes_file( diffsupplement='CVE-2015-123', accept=True )

    def test_decline_request_1_changes_file3(self):
        # .changes with another malformed tag
        self.run_test_1_changes_file( diffsupplement='CVE-123-123', accept=False )



    def run_test_2_changes_files(self, diffsupplement1='', diffsupplement2='', accept=False):
        httpretty.register_uri(httpretty.GET,
                               osc.core.makeurl(APIURL, ['source', "openSUSE:Factory", "ant", '_meta'], {}),
                               match_querystring = True,
                               body = """<package name="ant" project="openSUSE:Factory">
  <title>Antlr Task for ant</title>
  <description>Apache Ant is a Java-based build tool. In theory, it is kind of like
Make, but without Make's wrinkles.

Why another build tool when there is already make, gnumake, nmake, jam,
and others? Because all those tools have limitations that Ant's
original author could not live with when developing software across
multiple platforms. Make-like tools are inherently shell-based--they
evaluate a set of dependencies then execute commands, not unlike what
you would issue in a shell. This means that you can easily extend these
tools by using or writing any program for the OS that you are working
on. However, this also means that you limit yourself to the OS, or at
least the OS type, such as Unix, that you are working on.

Makefiles are inherently evil as well. Anybody who has worked on them
for any time has run into the dreaded tab problem. "Is my command not
executing because I have a space in front of my tab???" said the
original author of Ant way too many times. Tools like Jam took care of
this to a great degree, but still have yet another format to use and
remember.

Ant is different. Instead of a model where it is extended with
shell-based commands, Ant is extended using Java classes. Instead of
writing shell commands, the configuration files are XML-based, calling
out a target tree where various tasks are executed. Each task is run by
an object that implements a particular task interface.

Granted, this removes some of the expressive power that is inherent by
being able to construct a shell command such as `find . -name foo -exec
rm {}`, but it gives you the ability to be cross-platform--to work
anywhere and everywhere. If you really need to execute a shell command,
Ant has an &lt;exec&gt; task that allows different commands to be executed
based on the OS used.</description>
  <devel project="Java:packages" package="ant"/>
</package>""")

        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/292589",
            match_querystring = True,
            body = """
                <request id="292589">
                <action type="submit">
                  <source project="Java:packages" package="ant" rev="62"/>
                  <target project="openSUSE:Factory" package="ant"/>
                </action>
                <state name="review" who="mlin7442" when="2015-03-25T10:12:06">
                  <comment>Being evaluated by staging project "openSUSE:Factory:Staging:F"</comment>
                </state>
                <review state="accepted" when="2015-03-24T12:55:42" who="licensedigger" by_group="legal-auto">
                  <comment></comment>
                  <history who="licensedigger" when="2015-03-24T13:00:17">
                    <description>Review got accepted</description>
                  </history>
                </review>
                <review state="accepted" when="2015-03-24T12:55:42" who="factory-auto" by_group="factory-auto">
                  <comment>Check script succeeded</comment>
                  <history who="factory-auto" when="2015-03-24T12:58:05">
                    <description>Review got accepted</description>
                    <comment>Check script succeeded</comment>
                  </history>
                </review>
                <review state="accepted" when="2015-03-24T12:55:42" who="mlin7442" by_group="factory-staging">
                  <comment>Picked openSUSE:Factory:Staging:F</comment>
                  <history who="mlin7442" when="2015-03-25T10:12:06">
                    <description>Review got accepted</description>
                    <comment>Picked openSUSE:Factory:Staging:F</comment>
                  </history>
                </review>
                <review state="new" by_user="maintbot">
                  <comment>Please review sources</comment>
                </review>
                <review state="accepted" when="2015-03-24T12:58:05" who="factory-repo-checker" by_user="factory-repo-checker">
                  <comment>Builds for repo Java:packages/openSUSE_Tumbleweed</comment>
                  <history who="factory-repo-checker" when="2015-03-24T15:31:29">
                    <description>Review got accepted</description>
                    <comment>Builds for repo Java:packages/openSUSE_Tumbleweed</comment>
                  </history>
                </review>
                <review state="new" by_project="openSUSE:Factory:Staging:F">
                  <comment>Being evaluated by staging project "openSUSE:Factory:Staging:F"</comment>
                </review>
                <description>javapackages-tools update</description>
                </request>
            """)
           
        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/292589?withhistory=1",
            match_querystring = True,
            body = """
                <request id="292589">
                  <action type="submit">
                    <source project="Java:packages" package="ant" rev="62"/>
                    <target project="openSUSE:Factory" package="ant"/>
                  </action>
                  <state name="review" who="mlin7442" when="2015-03-25T10:12:06">
                    <comment>Being evaluated by staging project "openSUSE:Factory:Staging:F"</comment>
                  </state>
                  <review state="accepted" when="2015-03-24T12:55:42" who="licensedigger" by_group="legal-auto">
                    <comment></comment>
                    <history who="licensedigger" when="2015-03-24T13:00:17">
                      <description>Review got accepted</description>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-24T12:55:42" who="factory-auto" by_group="factory-auto">
                    <comment>Check script succeeded</comment>
                    <history who="factory-auto" when="2015-03-24T12:58:05">
                      <description>Review got accepted</description>
                      <comment>Check script succeeded</comment>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-24T12:55:42" who="mlin7442" by_group="factory-staging">
                    <comment>Picked openSUSE:Factory:Staging:F</comment>
                    <history who="mlin7442" when="2015-03-25T10:12:06">
                      <description>Review got accepted</description>
                      <comment>Picked openSUSE:Factory:Staging:F</comment>
                    </history>
                  </review>
                  <review state="new" by_user="maintbot">
                    <comment>Please review sources</comment>
                  </review>
                  <review state="accepted" when="2015-03-24T12:58:05" who="factory-repo-checker" by_user="factory-repo-checker">
                    <comment>Builds for repo Java:packages/openSUSE_Tumbleweed</comment>
                    <history who="factory-repo-checker" when="2015-03-24T15:31:29">
                      <description>Review got accepted</description>
                      <comment>Builds for repo Java:packages/openSUSE_Tumbleweed</comment>
                    </history>
                  </review>
                  <review state="new" by_project="openSUSE:Factory:Staging:F">
                    <comment>Being evaluated by staging project "openSUSE:Factory:Staging:F"</comment>
                  </review>
                  <history who="scarabeus_iv" when="2015-03-24T12:55:42">
                    <description>Request created</description>
                    <comment>javapackages-tools update</comment>
                  </history>
                  <history who="factory-auto" when="2015-03-24T12:58:04">
                    <description>Request got a new review request</description>
                    <comment>Please review sources</comment>
                  </history>
                  <history who="factory-auto" when="2015-03-24T12:58:05">
                    <description>Request got a new review request</description>
                    <comment>Please review build success</comment>
                  </history>
                  <history who="mlin7442" when="2015-03-25T10:12:06">
                    <description>Request got a new review request</description>
                    <comment>Being evaluated by staging project "openSUSE:Factory:Staging:F"</comment>
                  </history>
                  <description>javapackages-tools update</description>
                </request>
            """)
        
        self.req_292589_diff = '''
changes files:
--------------
--- ant-junit.changes
+++ ant-junit.changes
@@ -1,0 +2,5 @@
+Wed Mar 18 09:30:13 UTC 2015 - tchvatal@suse.com
+
+- Fix build with new javapackages-tools ''' + diffsupplement1 + '''
+
+-------------------------------------------------------------------
--- ant.changes
+++ ant.changes
@@ -1,0 +2,5 @@
+Wed Mar 18 09:30:13 UTC 2015 - tchvatal@suse.com
+
+- Fix build with new javapackages-tools ''' + diffsupplement2 + '''
+
+-------------------------------------------------------------------

spec files:
-----------
--- ant-antlr.spec
+++ ant-antlr.spec
@@ -1,7 +1,7 @@
 #
 # spec file for package ant-antlr
 #
-# Copyright (c) 2014 SUSE LINUX Products GmbH, Nuernberg, Germany.
+# Copyright (c) 2015 SUSE LINUX GmbH, Nuernberg, Germany.
 # Copyright (c) 2000-2009, JPackage Project
 # All rights reserved.
 #
@@ -654,11 +654,10 @@
 %{_mavenpomdir}/JPP-ant-launcher.pom
 %{_mavenpomdir}/JPP-ant-parent.pom
 %{_mavenpomdir}/JPP-ant.pom
-%config(noreplace) %{_mavendepmapfragdir}/*
+%{_datadir}/maven-metadata/ant.xml
 %dir %{_mavenpomdir}
 
 %endif
-
 %if %{with antlr}
 %files
 %defattr(0644,root,root,0755)
@@ -666,7 +665,7 @@
 %{ant_home}/lib/ant-antlr.jar
 %config(noreplace) %{_sysconfdir}/ant.d/antlr
 %{_mavenpomdir}/JPP.ant-ant-antlr.pom
-%config %{_mavendepmapfragdir}/ant-antlr
+%{_datadir}/maven-metadata/ant-antlr.xml
 %dir %{_mavenpomdir}
 %endif
 
@@ -676,10 +675,10 @@
 %{_javadir}/ant/ant-junit*.jar
 %{ant_home}/lib/ant-junit*.jar
 %config(noreplace) %{_sysconfdir}/ant.d/junit
-%config(noreplace) %{_mavendepmapfragdir}/ant-junit
 %{ant_home}/etc/junit-frames.xsl
 %{ant_home}/etc/junit-noframes.xsl
 %{_mavenpomdir}/JPP.ant-ant-junit*.pom
+%{_datadir}/maven-metadata/ant-junit.xml
 %dir %{_mavenpomdir}
 %endif
 
--- ant-junit.spec
+++ ant-junit.spec
@@ -1,7 +1,7 @@
 #
 # spec file for package ant-junit
 #
-# Copyright (c) 2014 SUSE LINUX Products GmbH, Nuernberg, Germany.
+# Copyright (c) 2015 SUSE LINUX GmbH, Nuernberg, Germany.
 # Copyright (c) 2000-2009, JPackage Project
 # All rights reserved.
 #
@@ -654,11 +654,10 @@
 %{_mavenpomdir}/JPP-ant-launcher.pom
 %{_mavenpomdir}/JPP-ant-parent.pom
 %{_mavenpomdir}/JPP-ant.pom
-%config(noreplace) %{_mavendepmapfragdir}/*
+%{_datadir}/maven-metadata/ant.xml
 %dir %{_mavenpomdir}
 
 %endif
-
 %if %{with antlr}
 %files
 %defattr(0644,root,root,0755)
@@ -666,7 +665,7 @@
 %{ant_home}/lib/ant-antlr.jar
 %config(noreplace) %{_sysconfdir}/ant.d/antlr
 %{_mavenpomdir}/JPP.ant-ant-antlr.pom
-%config %{_mavendepmapfragdir}/ant-antlr
+%{_datadir}/maven-metadata/ant-antlr.xml
 %dir %{_mavenpomdir}
 %endif
 
@@ -676,10 +675,10 @@
 %{_javadir}/ant/ant-junit*.jar
 %{ant_home}/lib/ant-junit*.jar
 %config(noreplace) %{_sysconfdir}/ant.d/junit
-%config(noreplace) %{_mavendepmapfragdir}/ant-junit
 %{ant_home}/etc/junit-frames.xsl
 %{ant_home}/etc/junit-noframes.xsl
 %{_mavenpomdir}/JPP.ant-ant-junit*.pom
+%{_datadir}/maven-metadata/ant-junit.xml
 %dir %{_mavenpomdir}
 %endif
 
--- ant.spec
+++ ant.spec
@@ -1,7 +1,7 @@
 #
 # spec file for package ant
 #
-# Copyright (c) 2014 SUSE LINUX Products GmbH, Nuernberg, Germany.
+# Copyright (c) 2015 SUSE LINUX GmbH, Nuernberg, Germany.
 # Copyright (c) 2000-2009, JPackage Project
 # All rights reserved.
 #
@@ -653,11 +653,10 @@
 %{_mavenpomdir}/JPP-ant-launcher.pom
 %{_mavenpomdir}/JPP-ant-parent.pom
 %{_mavenpomdir}/JPP-ant.pom
-%config(noreplace) %{_mavendepmapfragdir}/*
+%{_datadir}/maven-metadata/ant.xml
 %dir %{_mavenpomdir}
 
 %endif
-
 %if %{with antlr}
 %files
 %defattr(0644,root,root,0755)
@@ -665,7 +664,7 @@
 %{ant_home}/lib/ant-antlr.jar
 %config(noreplace) %{_sysconfdir}/ant.d/antlr
 %{_mavenpomdir}/JPP.ant-ant-antlr.pom
-%config %{_mavendepmapfragdir}/ant-antlr
+%{_datadir}/maven-metadata/ant-antlr.xml
 %dir %{_mavenpomdir}
 %endif
 
@@ -675,10 +674,10 @@
 %{_javadir}/ant/ant-junit*.jar
 %{ant_home}/lib/ant-junit*.jar
 %config(noreplace) %{_sysconfdir}/ant.d/junit
-%config(noreplace) %{_mavendepmapfragdir}/ant-junit
 %{ant_home}/etc/junit-frames.xsl
 %{ant_home}/etc/junit-noframes.xsl
 %{_mavenpomdir}/JPP.ant-ant-junit*.pom
+%{_datadir}/maven-metadata/ant-junit.xml
 %dir %{_mavenpomdir}
 %endif
 

other changes:
--------------
'''

        result = { 'state_accepted' : None }

        def change_request(result, method, uri, headers):
            u = urlparse.urlparse(uri)
            if u.query == 'cmd=diff':
                return (200, headers, self.req_292589_diff)
            if u.query == 'newstate=accepted&cmd=changereviewstate&by_user=maintbot':
                result['state_accepted'] = True
            elif u.query == 'newstate=declined&cmd=changereviewstate&by_user=maintbot':
                result['state_accepted'] = False
            return (200, headers, '<status code="ok"/>')

        httpretty.register_uri(httpretty.POST,
            APIURL + "/request/292589",
            body = lambda method, uri, headers: change_request(result, method, uri, headers))

        # first time request is in in review
        self.checker.set_request_ids(['292589'])
        self.checker.check_requests()

        self.assertEqual( result['state_accepted'], accept )


    def test_decline_request_2_changes_file(self):
        # two .changes files without tags
        self.run_test_2_changes_files( diffsupplement1='', diffsupplement2='', accept=False )

    def test_accept_request_2_changes_file(self):
        # both .changes files with correct tags
        self.run_test_2_changes_files( diffsupplement1='bnc#123456', diffsupplement2='CVE-2015-1234', accept=True )

    def test_decline_request_2_changes_file2(self):
        # one .changes file with correct tag and the other file without any
        self.run_test_2_changes_files( diffsupplement1='fate#123456', diffsupplement2='', accept=False )

    def test_decline_request_2_changes_file3(self):
        # one .changes file with correct tag and the other with a malformed tag
        self.run_test_2_changes_files( diffsupplement1='fate#1234', diffsupplement2='boo# ', accept=False )



if __name__ == '__main__':
    unittest.main()

# vim: sw=4 et
