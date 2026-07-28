import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    property string title: qsTr("AI Chat")
    required property var chatController

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 4
        spacing: 4

        ListView {
            id: messageView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: chatController ? chatController.messages : null
            spacing: 6

            delegate: ColumnLayout {
                width: ListView.view.width
                spacing: 2

                Label {
                    text: role === "user" ? qsTr("You") : qsTr("Assistant")
                    font.bold: true
                    opacity: 0.7
                }
                Label {
                    Layout.fillWidth: true
                    text: content
                    wrapMode: Text.Wrap
                }
            }

            onCountChanged: positionViewAtEnd()
        }

        Label {
            Layout.alignment: Qt.AlignHCenter
            text: qsTr("Ask a question about your vault")
            opacity: 0.6
            visible: messageView.count === 0
        }

        BusyIndicator {
            Layout.alignment: Qt.AlignHCenter
            running: chatController ? chatController.busy : false
            visible: running
        }

        RowLayout {
            Layout.fillWidth: true

            TextField {
                id: inputField
                Layout.fillWidth: true
                placeholderText: qsTr("Ask a question…")
                enabled: chatController && !chatController.busy
                onAccepted: sendButton.clicked()
            }
            Button {
                id: sendButton
                text: qsTr("Send")
                enabled: chatController && !chatController.busy && inputField.text.length > 0
                onClicked: {
                    chatController.sendMessage(inputField.text)
                    inputField.text = ""
                }
            }
        }
    }
}
