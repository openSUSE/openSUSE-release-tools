<?php

const PRODUCT_PATTERN = '/^(10\.[2-3]|11\.[0-4]|12\.[1-3]|13\.[1-2]|42\.[1-3]|15\.[0-1]|tumbleweed)$/';

function product_filter($product)
{
  return (bool) preg_match(PRODUCT_PATTERN, $product);
}
