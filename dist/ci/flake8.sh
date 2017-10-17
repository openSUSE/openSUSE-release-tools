#!/bin/sh
# There are 3 levels: F (FATAL), E (ERRORS) and W(WARN)
# Everything is blacklisted here only due to excessive hits in existing code
# base. The ignores should be removed in the severity level (e.g. F first, then E, then W)
find -type f -name "*.py" -print | grep -v abichecker | grep -v openqa | \
    xargs flake8 --ignore=E501,E122,F401,F405,E302,E228,E128,E251,E201,E202,F811,E203,E305,F841,E265,E261,E266,E231,E712,E401,E126,E502,E222,E241,E711,E226,E125,E123,W293,W391,E731,E303,E101,E129,E227,E713,E225,E124,E402,E221,E127,E701,W601,E714,W503,E211
