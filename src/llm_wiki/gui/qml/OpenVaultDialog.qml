import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: root
    title: qsTr("Open Vault")
    modal: true
    standardButtons: Dialog.Ok | Dialog.Cancel
    anchors.centerIn: parent
    width: 420

    required property var controller

    onAccepted: controller.openVault(pathField.text)
    onOpened: pathField.text = ""

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        Label { text: qsTr("Vault directory") }
        TextField {
            id: pathField
            Layout.fillWidth: true
            placeholderText: qsTr("/home/user/my-vault")
        }
    }
}
