local image = "registry.opensuse.org/opensuse/tools/images/images/osrt/worker-obs";
local image_version = "latest";
local image_full = image + ':' + image_version;

{
  image: image_full,
}
