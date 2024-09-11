from defw_util import expand_host_list
import sys

expr = sys.argv[1]

expr = expr.split('=')[1]

nl = expand_host_list(expr)

print(nl[0])

