"""Cost attribution for LLM serving with prefix caching.

The claim this package exists to measure: with a prefix cache, what a request
costs is a function of the sequence it arrived in rather than of the request, so
a per request token count cannot be an attribution, and the attribution almost
everybody ships overcharges whoever arrived first.
"""

__version__ = "0.1.0"
