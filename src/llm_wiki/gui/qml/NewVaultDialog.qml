import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    title: qsTr("New Vault")
    modal: true
    standardButtons: Dialog.Ok | Dialog.Cancel
    anchors.centerIn: parent
    width: 420

    required property var controller

    onAccepted: controller.createVault(pathField.text, nameField.text, descriptionField.text)
    onOpened: {
        pathField.text = ""
        nameField.text = ""
        descriptionField.text = ""
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        Label { text: qsTr("Directory") }
        TextField {
            id: pathField
            Layout.fillWidth: true
            placeholderText: qsTr("/home/user/my-vault")
        }

        Label { text: qsTr("Name") }
        TextField {
            id: nameField
            Layout.fillWidth: true
            placeholderText: qsTr("My Vault")
        }

        Label { text: qsTr("Description") }
        TextField {
            id: descriptionField
            Layout.fillWidth: true
        }
    }
}
