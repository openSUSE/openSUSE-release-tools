# Copyright (C) 2014 SUSE Linux Products GmbH
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

from datetime import datetime
import re
from xml.etree import cElementTree as ET

from osc.core import http_DELETE
from osc.core import http_GET
from osc.core import http_POST
from osc.core import makeurl


class CommentAPI(object):
    def __init__(self, apiurl):
        self.apiurl = apiurl

    def _prepare_url(self, request_id=None, project_name=None,
                     package_name=None):
        """Prepare the URL to get/put comments in OBS.

        :param request_id: Request where to refer the comment.
        :param project_name: Project name where to refer comment.
        :param package_name: Package name where to refer the comment.
        :returns: Formated URL for the request.
        """
        url = None
        if request_id:
            url = makeurl(self.apiurl, ['comments', 'request', request_id])
        elif project_name and package_name:
            url = makeurl(self.apiurl, ['comments', 'package', project_name,
                                        package_name])
        elif project_name:
            url = makeurl(self.apiurl, ['comments', 'project', project_name])
        else:
            raise ValueError('Please, set request_id, project_name or / and package_name to add a comment.')
        return url

    def _comment_as_dict(self, comment_element):
        """Convert an XML element comment into a dictionary.
        :param comment_element: XML element that store a comment.
        :returns: A Python dictionary object.
        """
        comment = {
            'who': comment_element.get('who'),
            'when': datetime.strptime(comment_element.get('when'), '%Y-%m-%d %H:%M:%S %Z'),
            'id': comment_element.get('id'),
            'parent': comment_element.get('parent', None),
            'comment': comment_element.text,
        }
        return comment

    def get_comments(self, request_id=None, project_name=None,
                     package_name=None):
        """Get the list of comments of an object in OBS.

        :param request_id: Request where to get comments.
        :param project_name: Project name where to get comments.
        :param package_name: Package name where to get comments.
        :returns: A list of comments (as a dictionary).
        """
        url = self._prepare_url(request_id, project_name, package_name)
        root = root = ET.parse(http_GET(url)).getroot()
        comments = {}
        for c in root.findall('comment'):
            c = self._comment_as_dict(c)
            comments[c['id']] = c
        return comments

    def add_comment(self, request_id=None, project_name=None,
                    package_name=None, comment=None):
        """Add a comment in an object in OBS.

        :param request_id: Request where to write a comment.
        :param project_name: Project name where to write a comment.
        :param package_name: Package name where to write a comment.
        :param comment: Comment to be published.
        :return: Comment id.
        """
        if not comment:
            raise ValueError('Empty comment.')

        url = self._prepare_url(request_id, project_name, package_name)
        return http_POST(url, data=comment)

    def delete(self, comment_id):
        """Remove a comment object.
        :param comment_id: Id of the comment object.
        """
        url = makeurl(self.apiurl, ['comment', comment_id])
        return http_DELETE(url)

    def delete_children(self, comments):
        """Removes the comments that have no childs

        :param comments dict of id->comment dict
        :return same hash without the deleted comments
        """
        parents = []
        for comment in comments.values():
            if comment['parent']:
                parents.append(comment['parent'])

        for id_ in comments.keys():
            if id_ not in parents:
                self.delete(id_)
                del comments[id_]

        return comments

    def delete_from(self, request_id=None, project_name=None,
                    package_name=None):
        """Remove the comments related with a request, project or package.
        :param request_id: Request where to remove comments.
        :param project_name: Project name where to remove comments.
        :param package_name: Package name where to remove comments.
        :return: Number of comments removed.
        """
        comments = self.get_comments(request_id, project_name, package_name)
        while comments:
            comments = self.delete_children(comments)
        return True

    def delete_from_where_user(self, user, request_id=None, project_name=None,
                               package_name=None):
        """Remove comments where @user is mentioned.

        This method is used to remove notifications when a request is
        removed or moved to another project.
        :param user: User name where the comment will be removed.
        :param request_id: Request where to remove comments.
        :param project_name: Project name where to remove comments.
        :param package_name: Package name where to remove comments.
        :return: Number of comments removed.
        """
        for comment in self.get_comments(request_id, project_name, package_name).values():
            if comment['who'] == user:
                self.delete(comment['id'])
