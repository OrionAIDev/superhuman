# The hello-cli smoke fixture and autonomous smoke fixture are standalone projects,
# not part of the skill's own test suite. Keep pytest from collecting their
# test_*.py during the main run.
collect_ignore = ["fixtures", "autonomous"]
