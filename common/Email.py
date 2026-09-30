import win32com.client as win32

class Email():

    def __init__(self):
        pass

    @classmethod
    def createEmailBody(cls, text):
        """
        creates email body by checking whether passed text is a string or list
        :param text: string or list
        :return: emailBody as per HTML format
        """
        if isinstance(text, str):
            emailBody = text
        elif isinstance(text, list):
            i = 1
            emailBody = str()
            for line in text:
                emailBody = emailBody + '<BR>' + str(i) + ') ' + line
                i = i + 1
        elif isinstance(text, dict):
            i = 1
            emailBody = str()
            for key, value in text.items():
                emailBody = '<BR>' + emailBody + '<BR>' + "Error in Process Name: {}".format(key) + '<BR>'

                if isinstance(value, list):
                    for line in value:
                        emailBody = emailBody + str(i) + ')' + line + '<BR>'
                        i = i + 1
                elif isinstance(value, str):
                    emailBody = value

        else:
            emailBody = text

        return emailBody

    @classmethod
    def createRecipientList(cls, recipient):
        """
        From recipient list create outlook compatible To / cc section of mail
        :param recipient: str or list
        :return: TO section of the mail
        """

        if isinstance(recipient, list):
            to_Section = str()
            for line in recipient:
                if len(to_Section) == 0:
                    to_Section = line
                else:
                    to_Section = to_Section + ';' + line
        elif isinstance(recipient, str):
            to_Section = recipient
        else:
            to_Section = recipient

        return to_Section

    @staticmethod
    def send_email(text, subject, recipient):
        """
        This function will send mail.
        :param text: str; body of the mail
        :param subject: str; subject of the mail
        :param recipient: str; people whom to send mail - Example: "hkim@apollo.com; pbhargava@apollo.com"
        :return: Outlook mail will be send
        """

        emailBody = Email.createEmailBody(text)
        to_Section = Email.createRecipientList(recipient)

        outlook = win32.Dispatch('outlook.application')
        mail = outlook.CreateItem(0)
        mail.To = to_Section
        mail.Subject = subject
        mail.HtmlBody = emailBody
        # mail.Display()
        mail.send

    @staticmethod
    def display_email(text, subject, recipient):
        """
        This function will display mail.
        :param text: str; body of the mail
        :param subject: str; subject of the mail
        :param recipient: str / list; people whom to send mail - Example: "hkim@apollo.com; pbhargava@apollo.com"
        :return: Outlook mail will be send
        """

        emailBody = Email.createEmailBody(text)
        to_Section = Email.createRecipientList(recipient)

        outlook = win32.Dispatch('outlook.application')
        mail = outlook.CreateItem(0)
        mail.To = to_Section
        mail.Subject = subject
        mail.HtmlBody = emailBody
        mail.Display()
