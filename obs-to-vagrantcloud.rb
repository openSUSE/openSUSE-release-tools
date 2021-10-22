# frozen_string_literal: true

require 'vagrant_cloud'
require 'down/net_http'
require 'http'
require 'optparse'
require 'optparse/uri'
require 'open-uri'
require 'fileutils'

def ensure_box_present(box_from_obs, org)
  matching_boxes = org.boxes.select { |b| b.name == box_from_obs['name'] }
  matching_box = nil

  if matching_boxes.empty?
    matching_box = org.add_box(box_from_obs['name'])
    matching_box.private = false
    matching_box.description = box_from_obs['description']
    matching_box.short_description = box_from_obs['short_description']
  elsif matching_boxes.length > 1
    raise "Got #{matching_boxes.length} matching boxes, but should have gotten at most one"
  else
    matching_box = matching_boxes[0]
  end

  matching_box
end

def link_box(box_from_obs, org)
  matching_box = ensure_box_present(box_from_obs, org)
  matching_box.versions.each(&:delete)

  matching_box.versions = []
  matching_box.save

  box_from_obs['versions'].each do |version|
    ver = matching_box.add_version(version['version'])
    ver.description = version['description']

    version['providers'].each do |provider|
      prov = ver.add_provider(provider['name'])
      prov.url = provider['url']
    end

    ver.save
    ver.release
  end
  matching_box.save

  matching_box
end

##
# Uploads the box specified in the hash box_from_obs to VagrantCloud using the
# supplied organization.
def upload_non_existent_boxes(box_from_obs, organization, provider_to_upload)
  matching_box = ensure_box_present(box_from_obs, organization)

  box_from_obs['versions'].each do |version|
    matching_versions = matching_box.versions.select { |v| v.version == version['version'] }
    matching_version = nil

    if matching_versions.length == 1
      matching_version = matching_versions[0]
    elsif matching_versions.empty?
      matching_box.save
      matching_version = matching_box.add_version(version['version'])
      matching_version.description = version['description']
    else
      raise "Got #{matching_versions.length} matching versions, but should have gotten one or none"
    end

    provider_added = false

    not_present_providers = version['providers'].select do |prov_from_obs|
      matching_version.providers.select { |p| p.name == prov_from_obs['name'] }.empty?
    end
    unless provider_to_upload.nil?
      not_present_providers = not_present_providers.select { |p| p['name'] == provider_to_upload }
    end

    not_present_providers.each do |provider|
      prov = matching_version.add_provider(provider['name'])
      begin
        box_dest = Down::NetHttp.download(provider['url'], max_redirects: 20)
        matching_box.save
        prov.upload(path: box_dest.path)
        provider_added = true
      ensure
        box_dest.close
        box_dest.unlink
      end
    end

    matching_version.release if provider_added && !matching_version.released?
  end
  matching_box
end

env_var = 'ATLAS_TOKEN'

raise "Environment variable #{env_var} is required" if ENV[env_var].nil?

options = {}
OptionParser.new do |opts|
  opts.on('--url URI', 'URL to json file published on OBS') do |u|
    options[:url] = u
  end

  opts.on('--organization ORG', 'organization/publisher of the vagrant box') do |p|
    options[:publisher] = p
  end

  opts.on('-n NAME', '--new-box-name NAME', 'alternative name for the Vagrant box') do |n|
    options[:name] = n
  end

  opts.on('-p PROVIDER', '--provider PROVIDER', 'only upload the supplied provider (unsupported for linking!)') do |p|
    options[:provider] = p
  end

  opts.on('-l', '--link', 'just link the box to from OBS and don\'t upload it') do
    options[:link] = true
  end
end.parse!

raise 'An organization must be provided' if options[:publisher].nil?

options[:link] = false if options[:link].nil?
raise 'Linking only a single provider is not supported' if options[:link] && !options[:provider].nil?

box_json = JSON.parse(HTTP.get(options[:url]))

account = VagrantCloud::Account.new(access_token: ENV[env_var])
publisher = account.organization(name: options[:publisher])

box_json['name'] = options[:name] unless options[:name].nil?

if options[:link]
  link_box(box_json, publisher)
else
  upload_non_existent_boxes(box_json, publisher, options[:provider])
end
