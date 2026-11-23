"""Deterministic category interleaving matching OttoFlow's distribution rule."""
from collections import defaultdict, deque


def distribute(operations, days):
    if not days:
        raise ValueError('Schedule days are required')
    groups=defaultdict(deque)
    for operation in operations:
        groups[str(operation.get('category') or '')].append(operation)
    ordered=[]
    while groups:
        for category in sorted(tuple(groups)):
            ordered.append(groups[category].popleft())
            if not groups[category]:
                del groups[category]
    counts=[0]*len(days)
    result=[]
    for operation in ordered:
        index=min(range(len(days)), key=lambda index:(counts[index], index))
        counts[index]+=1
        result.append((operation,days[index]))
    return result
