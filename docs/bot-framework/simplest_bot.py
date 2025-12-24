#!/usr/bin/python3
import sys
import ReviewBot


class MyBot(ReviewBot.ReviewBot):
    """Your custom bot implementation."""

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)
        # Configure bot options here
        self.request_default_return = None

    def check_source_submission(self, src_project, src_package, src_rev,
                                target_project, target_package):
        """
        Main review logic - override this method in your bot!
        This is called for each source submit/pull request.
        """

        # Information messages are visible at stdout when using "--verbose" option
        self.logger.info(f"Checking {src_package}: {src_project} -> {target_project}")

        # Your validation logic here
        if self._validate(src_project, src_package, src_rev):
            self.review_messages['accepted'] = 'Validation passed'
            return True
        else:
            self.review_messages['declined'] = 'Validation failed'
            return False

    def _validate(self, src_project, src_package, src_rev):
        """Your custom validation logic."""

        # Add your checks here
        return True


class CommandLineInterface(ReviewBot.CommandLineInterface):
    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = MyBot


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
